---
title: "x86_64 CI runners — the fallback pool's second architecture (#5215)"
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: ci-runner-pool, ci-headroom-guards
aboutObjects: tortoise, tools/install-ci-runner-x64.sh, scripts/ci_runner_gate.sh
created: 2026-10-22
updated: 2026-10-23
---

# x86_64 CI runners — the fallback pool's second architecture

Operator runbook for the `pi-runner-x64-*` self-hosted Actions runners. They exist to add
capacity to the SAME fallback pool the five ARM64 runners serve, without renaming a job,
changing a `runs-on:`, or narrowing which jobs may use the ARM64 pool (#5215).

## The finding: ARM64 was never a requirement

Every pool-eligible job in this repo asks for a **label**, never an architecture:

| `runs-on:` | count | who claims it |
| --- | --- | --- |
| `ubuntu-latest` | 77 | GitHub-hosted **and** (while the fallback is engaged) the self-hosted pool |
| `ubuntu-24.04` | 9 | **GitHub-hosted, plus — as a temporary mitigation since 2026-10-29 — the self-hosted pool.** Pinned to hosted on purpose (#6897/#7867); the pin deadlocks while hosted is unavailable, so the pool now claims it too (see the label contract). |

Counts are JOBS (not files), re-measured 2026-10-29 on this branch (`yaml.safe_load` each workflow,
count each job's `runs-on`): **77 / 9** — the same label set `--status`'s `label-coverage` block
reads (it counted 85 / 12 before it stripped whole-line comments), though that block reports RUNNER
counts per label, not job counts: measured on the live pool it prints `ubuntu-24.04 = 7 of 7
runner(s)` (a bare version pin is measured against the x64 sub-pool, its own denominator) and
`ubuntu-latest = 0 of 12` (a report — the gate is simply not routing that label here). The 77 / 9
figure above counts only jobs whose `runs-on` names a single `ubuntu-*` label; `--status` reads
every documented `runs-on` form (scalar, array, block sequence, and a `group:`/`labels:` mapping)
and refuses with `exit 4` rather than guess on an expression it cannot resolve to a static label
set — including when the expression is only ONE member of a list that otherwise parses, since a
parsed sibling must not answer for an unparsed one (GitHub documents both the combined
`group:`/`labels:` form and expressions in `labels:`). The `ubuntu-latest` row was previously
written as 83 against a measured 79, so treat both rows as a dated measurement and re-derive them
rather than trusting the numbers.

The architecture labels are **derived by GitHub from the host at registration** and cannot be
set by hand: the ARM64 VMs register `self-hosted, Linux, ARM64, self-hosted-arm64, ubuntu-latest`,
and this box registers `self-hosted, Linux, X64, self-hosted-x64, ubuntu-latest`. So:

- `ARM64`/`X64` are **ornaments** — no job matches on them, and none can (a `runs-on` naming
  either would have to name `self-hosted` too, which the repo forbids: `.github/workflows/python-ci.yml:1761`
  is "Do NOT change it back to `ubuntu-latest`, and never to `self-hosted`", and the reason is
  recorded at `:1748-1750` — a `self-hosted` pin removes the GitHub-hosted half of the race, so
  during a hosted outage the required leg WAITS and "the aggregate stays pending — not answered
  yet" instead of being answered by the pool).
- `ubuntu-latest` is a **custom label** on the pool. `scripts/ci_runner_gate.sh:289`
  (`fallback_on`) attaches it, `:290` (`fallback_off`) detaches it.
- The ARM64-ness is an accident of the first runner's hardware (OrbStack VMs on an
  Apple-Silicon Mac — `mirror-images.yml:104` records `orbctl run -m pi-runner uname -m` →
  `aarch64`). No workflow depends on an arm64 binary, container image, or wheel: the only
  arch-sensitive job in the repo (`mirror-images`) copies a **manifest list** and is pinned to
  a hosted label for an unrelated reason (a fresh egress IP).

**Contradiction test.** The recorded decision is that the local pool is **fallback-only**
(`scripts/ci_runner_gate.sh:1-30`: jobs landing on local hardware compete with the agent fleet
and the Tortoise graph — a local pytest run crashed FalkorDB, 178 restarts, 2026-10-12). Nothing
here reverses it: the installer does not bake `ubuntu-latest` in at registration. `--fallback
auto` (the default) reads the ARM64 pool's own state and attaches the fallback label to these
runners **only if that pool is carrying it right now**; `--fallback on|off` overrides explicitly,
and an unreadable state is a refusal (exit 2), never a guess. The gate's state stays the source
of truth for when the pool serves CI. What changes is only *which architecture* may serve the
fallback — which the decision never specified. The separate decision that pins legs to
`ubuntu-<version>` (the pool is ~2x slower and killed `test-slow` at its watchdog, #6897/#7867)
is **not** reversed by this section — it is **suspended while it deadlocks**, and only for the legs it
starves. The installer's default stays `off`, so it still does **not** claim any `ubuntu-<version>`
label unless an operator passes `CI_RUNNER_OS_LABEL=on|auto`; the live pool is opted IN as the
temporary mitigation described in the label contract above. Arm64 spellings GitHub also publishes
(`ubuntu-24.04-arm`, `ubuntu-22.04-arm`,
`ubuntu-26.04-arm`), which `_preserve_labels` drops too — the installer refuses to emit them, and
`tools/runner_autoscale.py::_preserve_labels` DROPS rather than preserves one on re-registration,
so an `-arm` attachment cannot survive a re-registration or
`tools/install-ci-runner-x64.sh --repair-labels`. That is NOT the same as "cannot become
permanent": `scripts/ci_runner_gate.sh` treats any non-auto label as `extra` (`:222`) and re-emits
it in its whole-set `PUT` body on every fallback transition (`:232`), and a runner nobody
re-registers keeps whatever label it was given — so re-measure.

⚠️ **The BARE `ubuntu-24.04` is a different case, and it is preserved — that is a correction.**
`_preserve_labels` used to drop it too ("the pool claims `ubuntu-latest` and never an
`ubuntu-<version>`"), which meant the scheduled autoscaler pass —
`tools/install-runner-autoscale-schedule.sh`, running with no operator watching — silently deleted
the mitigation from every re-registered runner, putting the `ubuntu-24.04` legs back to zero answers
and every PR back to BLOCKED, with no error and no log line. A fix that vanishes without a trace is
the failure this whole document exists to make visible, so the label is now **operator state**: the
installer attaches it under `CI_RUNNER_OS_LABEL=on|auto`, `--repair-labels` re-applies it, and
`CI_RUNNER_OS_LABEL=off --repair-labels` removes it on purpose. Nothing ASSERTS the LIVE label set —
`read_pool()` reads it on every autoscale pass, but no test and no gate compares it to the invariant
above — so re-measure `gh api repos/<owner>/<repo>/actions/runners` if a runner's labels are re-cut.

⚠️ **The OTHER half of the same erasure: capacity ADDS runners without the label.** Preserving it on
a re-registration only protects a runner that already has it. The autoscaler's FILL leg shells out to
`install-ci-runner-x64.sh --count N --fallback auto` (`runner_autoscale.py`'s `_run` passes no
`env=`), and the installer's default is `off` — so on a box whose timer carries no
`CI_RUNNER_OS_LABEL`, every runner the schedule **adds** joins without `ubuntu-24.04`. The pool then
drifts back to zero answers one fill at a time, and `--status` used to print "covered" at every step
until the last carrier was replaced. **Three pins** close it: `tools/install-runner-autoscale-schedule.sh`
renders `CI_RUNNER_OS_LABEL` into the timer (systemd unit, launchd plist, cron entry) **and** its
probe, captured from the operator's environment at install time and defaulting to `off`, so the
install command is the opt-in:

```bash
CI_RUNNER_OS_LABEL=on tools/install-runner-autoscale-schedule.sh   # the FILL leg then attaches it
```

and `--status` now reports `⚠️ PARTIAL` for any label only part of the pool answers, which is the
read that sees the drift before it reaches zero. The **third** pin is the ORPHANED re-registration
path, which used to be open: `runner_autoscale.py::resolve_labels` derived `arch[,ubuntu-latest]`
and never the OS fact, so a runner ABSENT from the pool — the one case with no labels to preserve —
was re-registered without `ubuntu-24.04`, silently, with no error and no log line. It now applies
the installer's own rule from the same `CI_RUNNER_OS_LABEL` (`_os_labels`), and a test runs the real
installer's `--print-labels` beside it across every arm so the mirror cannot drift again.
**Restore after a drift**, on each affected box:

```bash
CI_RUNNER_OS_LABEL=on tools/install-ci-runner-x64.sh --repair-labels --count <N>
```

⛔ `--repair-labels` PUTs a WHOLE label set (the `set` verb, not `add`), so the same command with the
variable **unset** — hence `off` — *removes* the mitigation from every runner it touches.

## The label contract — a label no runner answers silently blocks EVERY PR

**The rule, scoped — it is not "every label any workflow names".** Every `runs-on:` label that this
pool is **asked to serve** — `ubuntu-latest` (always, via the gate's fallback) and `ubuntu-24.04`
(while the OS-label mitigation below is on) — must have at least one **self-hosted** runner answering
it. Not "usually", not "when hosted is healthy": such a label can then only be served by GitHub-
**hosted**, and on this account hosted is effectively unavailable. ⛔ The absolute reading ("every
label must be answerable here") is wrong and out of scope for the labels this pool can never serve —
`ubuntu-24.04-arm` (an ARM64 image, on an x64 box: a false architecture claim, see
`_preserve_labels`), `macos-14`, `windows-2022`. Those are hosted-only by design and nobody is
starved by their not being answered here; `--status` reports them as measured
(`0 of N self-hosted runner(s) answer it`) rather than claiming a cause it did not measure.

> **OVERRIDES:** "a version-pinned hosted label (`ubuntu-<version>`) is claimed by no self-hosted
> runner" — the #6897/#7867 pin — is **suspended while hosted deadlocks**, for the labels this pool
> is asked to serve, because the alternative cost is a required aggregate that is never dispatched:
> no red, no green, no merges at all. The short version, for the lane that reads this before tidying
> it away: this departure is INTENTIONAL, it is recorded here and re-stated in `MEMORY.md`
> (`[ci-runners]`), and the revert is `CI_RUNNER_OS_LABEL=off` plus `--repair-labels`.
>
> **⚠️ INTENTIONAL IS NOT SETTLED.** #6897's freshest owner record (`2026-10-29T04:49:36Z`) measures
> this pool at **5.49×** hosted on `test-slow` with three watchdog kills and two `steps: 0` false
> reds in one 8-hour window, and recommends taking the label off the pool again — 13 minutes BEFORE
> the departure record on #9878 (`05:02:45Z`) that this marker is copied from, which does not answer
> it. Both are quoted and reconciled in *The decision this forces* above. Do not read this marker as
> a ruling that the suspension is right, and do not make it permanent: the default stays `off`, an
> explicit operator flag turns it on, and clearing the flag reverts it.

**Measured 2026-10-28**, in jobs completed by the runner that ran them: **one 60-minute window had 25
on self-hosted and 1 on hosted**; a later 30-minute window completed 92 jobs (3.1 jobs/min) with the
queue still holding 200+ jobs against ~12 runner slots and the oldest run waiting **8 hours**. Hosted
is not "slow here" — at that rate it cannot carry a required matrix at all.

**The incident it explains.** `workflow-lint` and `test-slow (a–e)` in `.github/workflows/python-ci.yml`
say `runs-on: ubuntu-24.04`. The self-hosted pool advertised `self-hosted-x64,ubuntu-latest` only, so
those six legs were *unschedulable*. `python-ci-gate` is the **required** aggregate whose `needs:`
includes every one of them, and a `needs:` job that never runs is never dispatched — so the gate never
reported, and **every tortoise PR stayed `BLOCKED`** for the whole day: no red, no green, nothing to
merge on. The gate was found queued **8 hours** on a run whose every other job had completed.

**The decision this forces, stated as a decision — and it is NOT closed.** Serving those legs from
this pool costs a **red on healthy code**; leaving them pinned costs a gate that is **never
dispatched**: no red, no green, and **no merges at all**. On 2026-10-28 the second cost was the one
being paid, so the live pool carries `ubuntu-24.04` as a temporary, explicitly-flagged mitigation —
and the installer's default stays `off`, so a fresh box does not silently reverse #6897.

⛔ **The measurement the departure is built on is FAVOURABLE, and the freshest owner record on
#6897 is not.** "The pool is ~2x slower" above is the 2026-10-28 reading. Re-measured like for like
on the same 365 nodeids (#6897, `daniel-ospina`, `2026-10-29T04:49:36Z`):

| leg | nodeids | class | pytest wall | job wall | conclusion |
| --- | --- | --- | --- | --- | --- |
| `test-slow (a)` | 365 | **GitHub-hosted** | **246.5 s** | 6.08 min | success |
| `test-slow (a)` | 365 | **pool** (`pi-runner-x64-1`) | **1352.5 s** — **5.49×** | 24.52 min | success |
| `test-slow (e)` | 850 | GitHub-hosted | 331.9–354.1 s | 7.57–7.98 min | success |
| `test-slow (e)` | 850 | pool | **killed at 2402 s, 375 of 850 nodeids done** | 41.48 min | **failure** |

plus three verbatim watchdog kills in one 8-hour window (`WATCHDOG: pytest killed after 40m (335
passed, 0 failed, 0 errored so far)` → `pytest exit code: 124`; `rc=137`; `41.27 min`) and two jobs
that held a pool runner for 10–11 minutes and executed **zero steps** before failing — a false red
of exactly the class #6897 was filed to remove. That comment closes: *"`test-slow` cannot be
scheduled onto the pool"*, *"Recommendation: take `ubuntu-24.04` off the x64 pool again and keep the
pin."*

**Both records are the owner's — the measurement at `04:49:36Z` and the departure record on #9878 at
`05:02:45Z`, which does not answer it — so this page states the open state instead of picking a
side.** Three consequences a reader must not miss:

- **The mitigation is TEMPORARY and reversible, and nothing in this change may entrench it.** The
  installer's default is `off`; `CI_RUNNER_OS_LABEL` is a per-box choice an operator sets, never a
  repo default; the capacity recipe below does not prescribe `on`; and the revert is one command
  (`CI_RUNNER_OS_LABEL=off … --repair-labels`, below). A lane that reads this page as licence to
  make the label permanent has misread it — that question belongs on #6897.
- **For `test-slow` the mitigation buys nothing.** It does not make the leg dispatchable; it turns a
  pending leg into a watchdog kill. What that leg needs is a budget matching the class (≥91 min
  measured, against a 60 min job cap) or a move — #6897's open question, and not something a label
  can fix.
- **`workflow-lint` is the leg this actually rescues** — measured indistinguishable on the pool
  (10–15 s, 5/5 success, against a 5 min cap) — and even that one is rescued at the Docker Hub quota
  cost listed below.

If the owner takes the recorded recommendation instead, the pool revert is
`CI_RUNNER_OS_LABEL=off` plus `--repair-labels` — still one command.

⛔ **The mitigation is label-WIDE, and SIX of the nine legs it now makes servable are pinned for
reasons that are NOT speed.** A label cannot be scoped to "the legs it starves": the moment the pool
carries `ubuntu-24.04`, all **nine** `runs-on: ubuntu-24.04` jobs become eligible for it. Six of
those pins exist to keep the job OFF the fleet, and none is about the watchdog:

- `.github/workflows/mirror-images.yml` — *"a hosted runner has its own egress IP, so these pulls
  cannot spend the quota the self-hosted fleet shares; running it on the fleet would spend the very
  quota it exists to stop spending."* The pool paying this cost gets **no** deadlock relief in
  return: `mirror-images` is a nightly cron/`workflow_dispatch` job, starved by nothing. Its
  in-file comment asserted that guarantee as current and was corrected in the same change.
- `.github/workflows/python-ci.yml` (`workflow-lint`) — its comment used to reason that
  *"`ubuntu-24.04` is claimed by no self-hosted runner"*, which this mitigation makes **false**;
  it was corrected in the same change. The leg pulls `rhysd/actionlint` by a single literal
  reference, so on the pool it spends the fleet's shared anonymous Docker Hub quota — a rate-limit
  red on healthy code, a failure class the #6897 analysis does not cover.
- **The four fleet OBSERVERS** — `.github/workflows/availability-liveness.yml`,
  `availability-watchdog.yml`, `ci-check-repair.yml`, `ci-queue-health.yml` — are pinned for a third
  and distinct reason: **topology, not egress or quota**. A guard whose job is to report on a full
  pool must not run ON that pool; they can sit in `actions/checkout` behind the very backlog they
  measure and then die at their own `timeout-minutes` with the measuring step skipped. GitHub's
  label matching is cumulative and unscoped (`github/docs`, *using self-hosted runners in a
  workflow*: *"You can create custom labels and assign them to your self-hosted runners at any
  time"*; *"These labels operate cumulatively"*), so a runner carrying `ubuntu-24.04` is eligible for
  them with no other token required. During a hosted outage — the ONLY window in which the
  mitigation is engaged — the instruments that exist to surface that outage become pool-eligible and
  go quiet precisely when they matter. Their four in-file comment blocks asserted the invariant this
  change reverses and were corrected in the same change.

These are **accepted costs of the temporary mitigation, stated as costs**, and all six revert with
it. If the mitigation becomes permanent, the scoping fix is to put the starved required legs on a
label the pool answers and leave `mirror-images` / `workflow-lint` / the four observers on one only
hosted answers.

**The escape hatch, and the proof it works.** `ubuntu-24.04` is an **OS fact**, not a fallback label:
`CI_RUNNER_OS_LABEL=on` (or `=auto`, which derives it from `/etc/os-release`) rides it on every label
arm, so `--fallback off` cannot drop it. The 8-hour-stuck gate **dispatched within a minute** of the
label being added, on `pi-runner-cmux-x64-6`, and completed in 6 s.

**Verify it — do not assume it.** `tools/install-ci-runner-x64.sh --status` now ends with a
`label-coverage` block: it reads every `runs-on:` label out of the workflows and prints
`<label> = <n> of <m> runner(s)` — `m` being the runners that COULD answer it (the x64 pool for a
bare `ubuntu-<version>`, the arm64 pool for an `-arm` spelling, the whole pool otherwise), so a
two-architecture pool is not misread as erosion. When `0 < n < m` the line also prints `⚠️ PARTIAL`.
The EXIT is decided per CLASS, not "any zero-answer label":

- a bare `ubuntu-<version>` with **no** answer is verdicted **only when this pool is asked to serve
  it** — the per-label line prints `⛔ <label> = 0 of <m> self-hosted runner(s) answer it.` and the
  block-level remedy then prints `⛔ UNCOVERED label(s) above`, with exit `5` (a verdict, and it is
  bad). "Asked" is read from `CI_RUNNER_OS_LABEL` through the installer's own `resolve_os_label`, so
  `on` — and `auto` on a matching host — asks, while `off` (the shipped default, and `auto`
  declining) does not.
  ⛔ **THAT READ IS THIS SHELL'S INTENT, NOT THE POOL'S — so it can only ADD a verdict, never
  subtract one.** The bare `--status` this section tells you to verify with has the variable unset
  (⇒ `off`), and a shell variable cannot say what the last installer pass, the autoscaler timer, or
  a peer lane asked for. Two consequences, both of which this block now states instead of papering
  over:
  * **`n == 0` with the flag off cannot be NAMED.** It is consistent with BOTH "the #6897 pin
    applied correctly" (nothing to do) and "asked and gone" — the pool eroded to zero one fill at a
    time, which is the incident state. The block reports the measurement plus both readings, exit
    `0`. It used to assert the harmless one (`NOT the incident state`), which cleared exactly the
    erosion this block exists to surface — a fail-open in the blocking direction, in the default
    invocation. To get the verdict anyway, run `CI_RUNNER_OS_LABEL=on --status` (read-only).
  * **`n > 0` OUTVOTES THE FLAG.** A self-hosted runner's labels are explicitly assigned — GitHub's
    REST surface is *Add custom labels* / *Set custom labels* / *Remove all custom labels*, and
    registration labels come from `config.sh --labels`, so a runner never acquires one on its own.
    A measured carrier is therefore proof that SOME actor asked the pool to serve the label, and a
    shell variable cannot un-ask it;
- `ubuntu-latest` at zero is a **report** (exit `0`): while the gate is not routing here, a pool
  carrying no `ubuntu-latest` IS the healthy state;
- an `-arm` spelling, `macos-*` / `windows-*`, and a **custom** label at zero are reports (`0`) too —
  their hosted availability was never measured, so a zero there is not this block's verdict. A
  custom label that merely starts `ubuntu-<digits>` (`ubuntu-2204-tools`) is NOT the bare-version
  class: it is measured against the whole pool and reported, never verdicted. **The intent is also
  per LABEL, not per block:** `resolve_os_label` can only ever set `ubuntu-24.04`, so any OTHER bare
  pin (`ubuntu-22.04`) is a report even with the flag `on` — this script cannot apply it, and a red
  whose printed remedy cannot work is the false-red class this block refuses to create.

The remedy is class- AND intent-correct for the `⚠️ PARTIAL` arm too. A half-covered `ubuntu-24.04`
is "re-apply with `--repair-labels`" **only while the pool is asked to serve it**. With the flag off
the block does **not** prescribe either side: reaching that arm means it just MEASURED carriers, so
both readings fit — a leftover from an earlier `on` period (the revert removes it) or erosion while
the pool is still asked (re-apply) — and `--repair-labels` PUTs a WHOLE label set, so running it
with the variable unset would remove the label from the **last** carriers. The block says so out
loud and leaves the choice to you.

**Settling which one it is.** The pool's recorded intent is the environment the *scheduled* pass
runs with, not your shell's: `grep -n CI_RUNNER_OS_LABEL /etc/systemd/system/runner-autoscale.service`
(the systemd path; `SYSTEMD_UNIT_DIR` if you installed elsewhere) or the `CI_RUNNER_OS_LABEL` key in
`$HOME/Library/LaunchAgents/*runner-autoscale*.plist` on macOS. That line is rendered by
`tools/install-runner-autoscale-schedule.sh` from the mode the fill leg will actually use, and it is
absent when no provisioner was configured — absence is "not recorded", which is a third answer, not
"off".

(The class is decided ONCE, in the same awk that computes the denominator, so the two cannot
disagree — they used to, and a zero-carrier custom label exited `5` claiming every PR was blocked.)
The read-nothing state is exit `4` and lives in the exit-code list below.

Run it from the repo root. A fresh
box, a re-provisioned pool, or a new workflow with a new `runs-on:` value all show up there instead
of as an unexplained all-PR block — and so does the erosion below, **before** the last carrier of a
label is replaced: `3 of 12 runners answer it` is the shape that reaches zero answers one fill at a
time with nothing red in between. One read is one `--paginate`d pool listing, so a pool larger than
`per_page` cannot read as uncovered.

**Adding capacity (the second half).** One runner instance = one concurrent job, so extra instances
are the only lever on throughput besides trimming the matrix. ⛔ **There is one path that ADDS a
runner, and it is the installer** — `runner_autoscale.py` calls it "the one registration path" and
refuses to improvise a second, divergent one. **Re-registering an EXISTING runner is a second path,
and it does not go through the installer:** `tools/runner_autoscale.py::register()` invokes
`config.sh --unattended … --labels <labels>` directly for every DEAD/ORPHANED re-registration. That
is exactly why its label derivation (`resolve_labels` / `_os_labels`) is pinned against the
installer's by `test_resolve_labels_matches_the_installer_on_every_os_label_arm`, and why neither
side may grow a labelling rule the other does not have — the drift already happened once here,
silently.

```bash
# on the box: ensure slots 1..N exist, carrying the label set the contract requires.
# ⛔ CI_RUNNER_OS_LABEL is the BOX's existing choice, not part of the command: unset means `off`,
# which is the #6897 pin intact. Export it only on a box that is already carrying the temporary
# mitigation — the autoscaler's timer pins whatever the box was installed with (see the FILL leg
# above), so a scheduled fill needs no help here.
tools/install-ci-runner-x64.sh --count <N>
```

⛔ **NEVER hand-write a `config.sh --labels …` registration.** The 2026-10-28 mitigation was applied
that way, and this section used to print it as a procedure; it breaks three of the statements this
document makes. It bypasses `--print-labels`' resolution and the `CI_RUNNER_OS_LABEL` validation, so
a typo or an extra label is accepted there and refused everywhere else; it claims `ubuntu-24.04` with
the flag **unset**, so no `--status` read can tell that runner apart from one an operator opted in;
and it writes `ubuntu-latest` in **permanently**, when the fallback label is the gate's to attach and
detach. That day is reproducible from this paragraph and is not a recipe for the next one. It is
stickier than it looks, too: `_preserve_labels` now PRESERVES the bare `ubuntu-24.04`, so anything
written by hand survives every subsequent repair and autoscale pass — a mistake made there is
permanent as well.

⛔ **If a runner directory is ever copied by hand** (the shape the dismissed recipe used),
`.runner_migrated` counts as "already configured": a copied directory that keeps it (or `.runner`)
makes `config.sh` refuse with *"Cannot configure the runner because it is already configured"*.
Remove `.runner`, `.runner_migrated`, `.credentials`, `.credentials_rsaparams`, `.service`, `.env`,
`.path`, `_work` and `_diag` — not just `.runner`.

⛔ **Never start the same registration twice.** A `nohup`'d `run.sh` plus the systemd unit means two
listeners polling under one name: they steal each other's jobs and one exits mid-job. After
installing the unit, confirm **one** `Runner.Listener` per directory and kill the other.

## What is installed

| | |
| --- | --- |
| Runner names | `pi-runner-x64-1`, `pi-runner-x64-2` |
| Labels | `self-hosted`, `Linux`, `X64` (host-derived) + `self-hosted-x64` (custom, this arch's own) + `ubuntu-latest` while the fallback is engaged + `ubuntu-24.04` **only** when an operator opts in with `CI_RUNNER_OS_LABEL=on|auto` (default `off`; the live pool is opted in as the temporary mitigation described in the label contract above) |
| Directories | `/opt/gh-runner-x64` (instance 1), `/opt/gh-runner-x64-2` (instance 2) |
| Service units | `actions.runner.daniel-ospina-tortoise.pi-runner-x64-<N>.service` (system mode, **enabled**) |
| Version | pinned to the pool's `2.337.0`, tarball sha256 pinned in the installer |
| Concurrency | one job per runner instance (that is how the Actions agent works) |

## Sizing — why 2, not 1 and not 4

Measured on a real job on this box (2026-10-22, `legal-e2e`, job `119852676052`): a single
`python` process saturates one core (99.3%) with `apt` at 46% alongside, i.e. **~1.0–1.5 cores
per concurrent job**, plus the per-job docker/FalkorDB it starts. Measured at install time:
8 vCPU, load 0.30–1.78, 85–94% idle, 74–80 GB free, ~9 GB memory available.

| count | cores for CI | cores left for agent lanes (8 total) | verdict |
| --- | --- | --- | --- |
| 1 | ~1.5 | ~6.5 | works, leaves measured headroom unused while 120+ runs queue |
| **2** | **~3** | **~5** | **chosen** — doubles local capacity, keeps a wide margin for the lanes |
| 4 | ~6 | ~2 | refused by the installer's guard: would starve the lanes |

The installer encodes this as a **capacity guard** on CPU — `CORES_PER_RUNNER=2` +
`CORES_RESERVED_FOR_AGENT_LANES=2`, checked after fork headroom and disk in the harness's own
order. On this box `--count 4` refuses with *"4 runners need 4x2 cores + 2 reserved for agent
lanes = 10, this box has 8"*. This is a bound on **this box's CPU**, not a cap on the agent
lanes — no lane limit is set anywhere.

The CPU guard is a capacity bound; **the memory leg is not** — it reads `MemAvailable`
(`/proc/meminfo`), so it IS a live-load reading and it can refuse on a busy box (measured
2026-10-22: 6G available refused `--count 2`, which needs 8G). That is deliberate fail-closed —
two runners starting into 6G would OOM-kill the lanes' FalkorDB — but state it for what it is: of
the four legs (forks, disk, cpu, memory), memory is the one whose reading moves with load. Every
sizing unit is env-overridable (`CI_RUNNER_{CORES_PER_RUNNER,CORES_RESERVED,DISK_PER_RUNNER_GB,
DISK_RESERVE_GB,MEM_PER_RUNNER_GB,MEM_RESERVE_GB,FORKS_PER_RUNNER}`) so a bound can
be exercised on its own; such a value that is present and unparseable is refused as a usage
error (exit 1), never coerced to zero. The two HOST readings (`CI_RUNNER_FORK_CAP`,
`CI_RUNNER_NCPU` — pins for `ulimit -u` and the core count) are read, not sized, and an
unparseable one is a GUARD refusal (exit 2), the same class as an unreadable headroom bound.

## Recreate from scratch / after a reboot

The runner is a **systemd service**, enabled at install, so a reboot needs no human step.
Verify only:

```bash
systemctl is-enabled actions.runner.daniel-ospina-tortoise.pi-runner-x64-1.service   # enabled
systemctl is-active  actions.runner.daniel-ospina-tortoise.pi-runner-x64-1.service   # active
tools/install-ci-runner-x64.sh --status                                              # + the repo-side view
```

If a runner is gone (box rebuilt, disk lost, runner deleted), re-create it — this is the whole
procedure, and it is idempotent: an already-configured dir is reused, and the systemd unit is
re-INSTALLED only when `/etc/systemd/system/<unit>` is absent (`svc.sh install` runs
conditionally), then STARTED. Re-running therefore recovers a stopped service and a missing unit;
it does not call `systemctl enable` — the enable happened at the original install, and a unit that
was explicitly disabled by hand stays disabled until someone enables it):

```bash
tools/install-ci-runner-x64.sh --count 2            # what was run here
# → fallback: ENGAGED on the ARM64 pool now → labels=self-hosted-x64,ubuntu-latest
tools/install-ci-runner-x64.sh --count 2 --dry-run  # plan + headroom arithmetic only
```

On the day this was recorded the gate was in fallback mode (all five ARM64 runners carried
`ubuntu-latest`), so the new runners were registered with it too — which is why they took jobs
within twenty seconds. That is a **state read, not a default**: with GitHub-hosted healthy the
same command registers `self-hosted-x64` only, and the gate attaches the fallback label when it
falls back.

- `--count N` → `tools/install-ci-runner-x64.sh:977` (the scale loop) · the two mode blocks the
  paragraphs below cite: `install` `:799` (arch refusal dies at `:804`) and `uninstall` `:852` ·
  guards at `:799-849` (forks `:807`, disk `:827`, cpu/memory `:834` — `:847`, the memory guard,
  is the last one) · sha256 pin + registration at `:988-998` (verify `:988`, token `:991`, one
  argv `:996-997`, `config.sh` `:998`; the run at `:980` is the dry-run echo) · systemd
  install/start at `:1008-1010` · label resolution at `:222-319` (`fallback_engaged` `:222`,
  `resolve_os_label` `:250`, `resolve_labels` `:282`, whose body ends at its `}` on `:319`) ·
  `repair` `:739` · `--print-labels` `:772` · label-coverage at `:337-734` (section separator at
  `:337`, its `say` header at `:344`, the three "read nothing" arms at `:707`, `:715` and `:724`,
  the uncovered verdict at `:732`).
- `--count` is validated ONCE, before the mode dispatch, for EVERY mode (`:186-190`, the `case`
  through its `esac`): a `--count 0 --uninstall` is a usage error (exit 1), not a silent no-op
  that reports success having deregistered nothing. `CI_RUNNER_OS_LABEL` is validated the same
  way and in the same place (`:245-247`), so a typo is refused in `--status` and `--uninstall`
  too — not only in the three modes that resolve labels.
- **`--uninstall` deletes only what it can PROVE is a runner install, and only after its unit is
  gone** (`:851-960`; the proof is `is_runner_install` at `:268`, which accepts `.runner` or
  `bin/Runner.Listener` — a lone `config.sh` is NOT proof): one of those must exist in the directory, every target is validated BEFORE the first
  deletion, and every removal token is minted BEFORE the first deletion — so a refusal in any of
  those phases leaves every directory untouched, never a half-uninstalled set. `CI_RUNNER_BASE` is
  operator input, so "a directory exists at this path" is not evidence of a runner — the earlier
  gate was exactly that, and `CI_RUNNER_BASE=/opt --uninstall` would have run `sudo rm -rf /opt`.
  A validation refusal is exit 2 and nothing is removed. A target whose systemd unit cannot be
  removed (`svc.sh uninstall` fails while the unit is still installed, or the unit is installed and
  the directory has no `svc.sh`), **or whose `config.sh remove` fails**, is **left intact,
  registration and all**, and reported (exit 3): the unit would otherwise survive pointing at a
  deleted working directory, and a still-registered runner would lose the only local record that a
  retry — and `--status` — can read, so `--uninstall` reported a clean removal while exiting 0.
- **Exit codes** (`:65-86`; `--help` prints the same block): `0` ok · `1` usage · `2` guard
  refused — headroom, arch, an undecidable fallback, or an uninstall target that is not provably
  a runner, with nothing downloaded or registered, so re-run with a smaller `--count` · `3`
  operation failed — download, sha256 verify, extract, token mint, `config.sh`, `svc.sh` (including
  a unit removal that failed, whose target directory is then left intact, or a deregistration that
  failed, likewise left intact), a tool missing from PATH, or a failed remote read · `4`
  label-coverage could not read the workflows, so it has no verdict — no
  `.github/workflows/*.yml|*.yaml` where `--status` was run, workflows that name no
  single-label `runs-on:` at all, or a `runs-on:` with a member that is not a static label (a
  `${{ … }}` expression in the value OR in any single member of a list, or a spelling this parser
  does not understand). Nothing was read, so nothing was cleared: re-run `--status`
  from the repo root, because an empty read is not a coverage verdict — and neither is a PARTIAL
  one · `5` label-coverage WAS read and found a required label that **this pool is asked to serve**
  with no self-hosted answer — the erosion state: the operator opted in (`CI_RUNNER_OS_LABEL=on`,
  or `auto` resolving to this host), so the mitigation is silently absent. A zero-answer label the
  pool is **not** asked to serve (`off`, the shipped default, or `auto` declining) is a report, not
  a verdict: that is either #6897's pin applied correctly OR erosion to zero, which one shell cannot
  tell apart, so it reports both. **`0` does not mean "covered":** `0` is what a report with no ⛔
  line exits with, and a `0`-runner label the pool IS asked to serve is the `5` case, not a clean
  bill of health. Nothing in the tree keys on `--status`'s status today, so this is a signal for an
  operator or a future `--status && …` guard, not a change any caller currently reads.
- `--print-labels` answers BEFORE the arch and headroom guards, so the label invariant is
  checkable on any host (including the Apple-Silicon dev box and the ARM64 pool runners); the
  arch refusal still governs every INSTALL — and only an install (`:799`, the `MODE = install`
  block; the refusal itself dies at `:804`). `--status`, `--repair-labels` and `--print-labels` are
  **not** gated by the host's architecture: they reach only `gh`, download nothing and run no local
  binary, so a runner can be inspected or relabelled from any host.

  ⚠️ **`--uninstall` is only HALF arch-free, and that matters when decommissioning.** The arch guard
  does not refuse it, but the deregistration step is `./config.sh remove`, and the runner package's
  own `config.sh` execs the listener for that verb — `./bin/Runner.Listener "$@"` — and that binary
  is the x64 ELF the tarball extracts into the runner directory. On a foreign arch the exec fails
  ("Exec format error"), the unit has already been removed (deliberately, first), and the runner
  stays REGISTERED: the box is left half-decommissioned, and the failure is reported as exit 3 with
  the captured `config.sh` output in the warning. So to remove a runner from a non-x86_64 host: let
  it remove the unit, then deregister from an x86_64 host or delete the runner through the API.
  (Measured on an unpacked `actions-runner-linux-x64` tarball, whose `config.sh:76-81` is that exec;
  the version is a measurement detail, and the installed file is `$RUNNER_BASE/config.sh`.) (The
  guard used to sit above the uninstall block too, which refused decommissioning on exactly the
  ARM64 hosts the fallback pool runs on; `CI_RUNNER_ARCH` remains the seam if a host's arch must be
  asserted by hand.)
- `--fallback auto|on|off` decides whether `ubuntu-latest` is attached (auto follows the ARM64
  pool). `--print-labels` answers "which labels would a runner get" without touching a guard,
  token or the network — that is the seam the label invariant is tested through. `--repair-labels`
  re-applies the label set to already-registered runners: the repair for residual 1, needing no
  re-download and no re-registration. It is also **the restore verb for the OS label** — if
  `ubuntu-24.04` is missing from an already-registered runner (turned off, dropped by a fresh
  install, or removed by hand), `CI_RUNNER_OS_LABEL=on tools/install-ci-runner-x64.sh
  --repair-labels` puts it back on every runner in `CI_RUNNER_BASE`, and
  `CI_RUNNER_OS_LABEL=off … --repair-labels` takes it away again — both without re-downloading or
  re-registering anything.
- Requires: `gh` authenticated with admin on the repo (mints the registration and removal tokens),
  `curl`/`tar`/`sha256sum` for the pinned tarball, `sudo` for the system units — and `gh`, `unzip`
  and `docker` **on the runner's own PATH**, because the lane jobs need them (#9018: a pool runner
  missing them fails with exit 2, a false red rather than a queue delay). The installer warns
  about a missing job tool instead of silently installing a runner that will redden CI.
- Removing: `tools/install-ci-runner-x64.sh --uninstall --count 2` (deregisters, then deletes
  the dirs; `--dry-run` first).

## Verify it is actually serving

```bash
gh api repos/daniel-ospina/tortoise/actions/runners \
  --jq '.runners[]|select(.name|startswith("pi-runner-x64"))|{name,status,busy,labels:[.labels[].name]}'
```

A queued job proves nothing on its own — the artifact is the **job detail naming the runner**:

```bash
gh api "repos/daniel-ospina/tortoise/actions/runs/<RUN>/jobs?per_page=100&filter=all" \
  --jq '.jobs[]|select(.runner_name|test("x64"))|[.id,.name,.status,.conclusion,.runner_name]|@tsv'
```

Recorded on 2026-10-22 (queue: 75 tortoise + 48 agent-infra runs waiting, 5/5 ARM64 runners busy):

| run | job | job id | result | runner |
| --- | --- | --- | --- | --- |
| 39787188701 | `test-isolation` | 119853473239 | success | `pi-runner-x64-1` |
| 39787188701 | `license-surface` | 119853473352 | success | `pi-runner-x64-1` |
| 39787791937 | `sigpipe-guard` | 119855408820 | success | `pi-runner-x64-1` |
| 39786848662 | `legal-e2e` | 119852676052 | success | `pi-runner-x64-1` |
| 39786088684 | `test (c)` | 119851988988 | success | `pi-runner-x64-2` |

## Known residuals

The installer's invariants are pinned by `tests/test_ci_runner_x64_install.py` (registered in
`config/ci-surfaces.yml`'s `core` surface AND in `carve_out:`, which is the URI-unset leg it
belongs to). It drives the script as a subprocess in a hermetic env — a temp base dir, a stub
`gh`, no network — and every case runs on any host and any architecture; there is no skip path.

1. **A stamped runner now HEALS on the next gate tick — #2327 is FIXED; what remains is when the
   gate re-runs, and the manual re-sync.** The pre-#2327 gate rewrote the labels of *every* runner
   it could enumerate, which is what stamped `self-hosted-arm64` onto an x86 box and dropped
   `self-hosted-x64` — a false architecture claim a future arch-specific job could be routed into,
   with nothing in the API distinguishing it from an ARM64 box. `daniel-ospina/agent-infra#2327`
   landed as PR #2338 (gate commit `63b768f6d`, on `agent-infra` `main`): `managed_ids`
   (`scripts/ci_runner_gate.sh:187`) selects only runners carrying a pool marker, `labels_for`
   (`:212`) derives each body from THAT runner's own GitHub host label (`ARM64`/`X64`), and the
   legacy `self-hosted-arm64` stamp is deliberately NOT consulted — so an already-stamped x86
   runner is repaired rather than re-stamped. `ubuntu-latest` is the only label the gate owns and
   the only one it toggles (`fallback_on`/`fallback_off` `:289-290`).

   ⛔ **The trigger is still the next gate TICK, not the next transition, and that is the part that
   stays a risk.** The gate re-applies labels when `mode != want || prev != want` (`:379`), and
   `current_mode` (`:292`) counts *any* runner carrying `ubuntu-latest` as `local` — while these
   runners carry that label the gate cannot read `github`, so on a day GitHub-hosted is healthy it
   runs `fallback_off`, which post-#2327 strips `ubuntu-latest` from an x64 runner and emits
   `{self-hosted-x64}` (plus any custom label that runner already carried, which the gate never
   removes — `labels_for` `:218-233`). `tools/install-ci-runner-x64.sh --repair-labels` re-applies this arch's
   marker and syncs the fallback label to the gate's state by hand, with no re-registration.
2. **The pool is still the slower class for long legs.** These runners are not pinned off the
   fast matrix (`test (a)…(i)`), so a 20m watchdog leg can still be killed here if this box
   turns out slower than hosted under load. That is the #6897 measurement, unchanged — and while hosted
   is unavailable the version-pinned legs are exactly the ones this pool now has to serve, at that
   measured risk. The rest of the fast matrix is unaffected.
3. **`ubuntu-latest` remains ambiguous by design.** Any job asking for it can land on
   GitHub-hosted, an ARM64 runner, or one of these — that ambiguity is what the fallback is
   for, and why the legs that need a specific class are version-pinned.
