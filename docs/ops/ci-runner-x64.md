---
title: "x86_64 CI runners — the fallback pool's second architecture (#5215)"
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: ci-runner-pool, ci-headroom-guards
aboutObjects: tortoise, tools/install-ci-runner-x64.sh, scripts/ci_runner_gate.sh
created: 2026-10-22
updated: 2026-10-30
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
online runner(s)` (a bare version pin is measured against the x64 sub-pool, its own denominator,
and only ONLINE runners are counted — an offline runner cannot be handed a job) and
`ubuntu-latest = 0 of 12 online runner(s)` (a report — the gate is simply not routing that label
here). The 77 / 9
figure above counts only jobs whose `runs-on` names a single `ubuntu-*` label; `--status` reads
every documented `runs-on` form (scalar, array, block sequence, and a `group:`/`labels:` mapping)
and refuses with `exit 4` rather than guess on an expression it cannot resolve to a static label
set — including when the expression is only ONE member of a list that otherwise parses, since a
parsed sibling must not answer for an unparsed one (GitHub documents both the combined
`group:`/`labels:` form and expressions in `labels:`). The refusal is a **content** test, not a
prefix one: the expression may be the whole value (`${{ matrix.os }}`), one member of a list
(`[self-hosted, "${{ matrix.arch }}"]`) or only PART of a label
(`ubuntu-24.04-${{ matrix.arch }}`), and a token this parser cannot carry as one static label — a
space-bearing scalar, or a YAML alias (`runs-on: *pool`) — is refused for the same reason: the
`runs-on:` has no static label set, so there is nothing to verdict. The `ubuntu-latest` row was
previously written as 83 against a measured 79, so treat both rows as a dated measurement and
re-derive them rather than trusting the numbers.

The architecture labels are **derived by GitHub from the host at registration** and cannot be
set by hand: the ARM64 VMs register `self-hosted, Linux, ARM64, self-hosted-arm64, ubuntu-latest`,
and this box registers `self-hosted, Linux, X64, self-hosted-x64, ubuntu-latest`. So:

- `ARM64`/`X64` are **ornaments in DEFAULT routing** — no job in the default tree requests them, so
  nothing distinguishes the classes today. They are NOT unroutable: labels match cumulatively and a
  custom label routes on its own (that is how `ubuntu-latest` reaches the pool, next bullet), so a
  bare `runs-on: self-hosted-x64` DOES select this box. What the repo refuses **as a default** is
  not a spelling but a CLASS — any POOL-ONLY label: `self-hosted`, and equally the bare custom
  `self-hosted-x64` above, since no GitHub-hosted runner carries either. The `test-slow` pin's
  header in `.github/workflows/python-ci.yml` names the spelling ("Do NOT change it back to
  `ubuntu-latest`, and never to `self-hosted`"), and the reason it gives covers both: a pool-only
  pin removes the GitHub-hosted half of the race, so during a hosted outage the required leg WAITS
  and "the aggregate stays pending — not answered yet" instead of being answered by the pool.
  **One opt-in departure exists and is recorded below** — `CI_SHARD_ROUTING=pool-split` names
  `[self-hosted, self-hosted-x64]` on the ODD rows (`OVERRIDES:` + outage-wait exposure in that section).
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
is **not** reversed by this section — it is **suspended while it deadlocks**, and only for the
legs it starves. The installer's default stays `off`, so it still does **not** claim any
`ubuntu-<version>` label unless an operator passes `CI_RUNNER_OS_LABEL=on|auto` (`auto` claims it only
on a 24.04 x86_64 host); the live pool is opted IN as the temporary mitigation described in the label
contract above. The arm64 spellings GitHub also publishes (`ubuntu-24.04-arm`,
`ubuntu-22.04-arm`, `ubuntu-26.04-arm`) are never emitted. `tools/runner_autoscale.py::
_preserve_labels` DROPS the whole class on re-registration — reading NO flag, the way it always
has — so an attachment survives a repair that re-applies it (`--repair-labels` keeps only the box's own label under
`on|auto`, see "TWO NAMED CARVE-OUTS" below) and does NOT survive a re-registration. That is NOT
the same as "cannot become
permanent": `scripts/ci_runner_gate.sh` treats any non-auto label as `extra` (`:222`) and re-emits
it in its whole-set `PUT` body on every fallback transition (`:232`), and a runner nobody
re-registers keeps whatever label it was given — so re-measure.

⚠️ **The BARE `ubuntu-24.04` is the pin this whole contract is about, and `_preserve_labels` still
DROPS it.** That is deliberate, not an oversight: it is the label a leg pins to ESCAPE this pool, so
preserving it on re-registration is how an attachment out of band becomes permanent. ⛔ THE COST IS
NAMED RATHER THAN HIDDEN: while the #6897 mitigation is engaged this pool legitimately carries it, so
a runner the autoscaler RE-registers loses a pin it had, and nothing in `runner_autoscale.py`
re-attaches it. `--repair-labels` does (it KEEPS `ubuntu-24.04` under `on|auto`, and drops the rest
of the family), a fresh install does
(the installer's `resolve_labels` emits it), the ORPHANED derivation does (`_os_labels`, below), and
`--status`' `label-coverage` exits **5** when the pool is ASKED for the label and answers with none —
so the loss is REPORTED, not silent. Nothing ASSERTS the LIVE label set — `read_pool()` reads it on
every autoscale pass, but no test and no gate compares it to the invariant above — so re-measure
`gh api repos/<owner>/<repo>/actions/runners` if a runner's labels are re-cut.

⚠️ **The OTHER half of the same erasure: capacity ADDS runners without the label.** A re-registration
path only ever reaches a runner that already exists. The autoscaler's FILL leg shells out to
`install-ci-runner-x64.sh --count N --fallback auto` (`runner_autoscale.py`'s `_run` passes no
`env=`), and the installer's default is `off` — so on a box whose timer carries no
`CI_RUNNER_OS_LABEL`, every runner the schedule **adds** joins without `ubuntu-24.04`. The pool then
drifts back to zero answers one fill at a time, and `--status` used to print "covered" at every step
until the last carrier was replaced. **Three pins** close it: `tools/install-runner-autoscale-schedule.sh`
renders `CI_RUNNER_OS_LABEL` into the timer (systemd unit, launchd plist, cron entry) **and** its
probe, captured from the operator's environment at install time and defaulting to `off`, so the
install command is the opt-in. The render is **unconditional**: gating it on a provisioner that
resolved left the third pin below inert on the boxes where it is the only repair there is.

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

⛔ `--repair-labels` **UNIONS** — it adds labels and cannot strip `ubuntu-latest` or a custom label
a workflow targets (see *`--repair-labels` UNIONS* below) — but the version pin is one of its two
NAMED carve-outs, and that carve-out is this flag's `off` state. So the same command with the
variable **unset** — hence `off` — does **not** re-apply `ubuntu-24.04`, and it *removes* the pin from
every runner it touches. That is why the restore command above spells the flag out.

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
(`0 of N online self-hosted runner(s) answer it`) rather than claiming a cause it did not measure.

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

⛔ **The measurement the departure is built on is FAVOURABLE, and the freshest owner record is
not.** On **#6897**, "The pool is ~2x slower" above is the 2026-10-28 reading. Re-measured like for
like on the same 365 nodeids (#6897, `daniel-ospina`, `2026-10-29T04:49:36Z`):

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

⛔ **The mitigation is label-WIDE, and EIGHT of the nine legs it now makes servable are pinned for
reasons that are NOT speed.** A label cannot be scoped to "the legs it starves": the moment the pool
carries `ubuntu-24.04`, all **nine** `runs-on: ubuntu-24.04` jobs become eligible for it. Eight of
those pins exist to keep the job OFF the fleet, and none is about the watchdog — `test-slow` is the
only leg whose pin is about speed, and the mitigation buys it nothing (above):

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
- `.github/workflows/finding-provenance.yml` — **both** of its jobs (`classify`, `provenance`) are
  pinned on a fourth reason: **the runner tool contract (#9018)**, not egress, quota or topology.
  The pool's service `PATH` is a minimal non-login one with no `gh`, so a pool-served run reds as
  `gh: command not found` / 127 — a red that reads as a product failure and is not. Measured
  2026-10-24: 38 of the 39 `classify` failures were on `pi-runner-vm-*` holding this same label
  (run 40141219586). Both jobs now carry `runner-tools.sh ensure gh`, which provides the tool or
  fails NAMING it — so the tool gap is closed by that step, and the pin is defence in depth, not the
  mitigation of it. Its in-file comment also stated the
  invariant as current ("a version-pinned label no self-hosted runner claims") and was corrected in
  the same change.

These are **accepted costs of the temporary mitigation, stated as costs**, and all eight revert with
it. If the mitigation becomes permanent, the scoping fix is to put the starved required legs on a
label the pool answers and leave `mirror-images` / `workflow-lint` / the four observers /
`finding-provenance` on one only hosted answers.

**The escape hatch, and the proof it works.** `ubuntu-24.04` is an **OS fact**, not a fallback label:
`CI_RUNNER_OS_LABEL=on` (or `=auto`, which derives it from `/etc/os-release`) rides it on every label
arm, so `--fallback off` cannot drop it. The 8-hour-stuck gate **dispatched within a minute** of the
label being added, on `pi-runner-cmux-x64-6`, and completed in 6 s.

**Verify it — do not assume it.** `tools/install-ci-runner-x64.sh --status` now ends with a
`label-coverage` block: it reads every `runs-on:` label out of the workflows and prints
`<label> = <n> of <m> online runner(s)` — `m` being the ONLINE runners that COULD answer it (the
x64 pool for a bare `ubuntu-<version>`, the arm64 pool for an `-arm` spelling, the whole pool
otherwise), so a two-architecture pool is not misread as erosion. When `0 < n < m` the line also
prints `⚠️ PARTIAL`. ⛔ **An OFFLINE runner is not an answer** — GitHub defines it as "not connected
to GitHub", so it cannot be handed a job — so offline carriers are excluded from BOTH sides of the
ratio and printed as context instead (``Offline, so NOT counted as an answer: K runner(s) in the pool
are not `online`, J of them carrying <label>``). Without that split, 7 carriers all offline
printed `7 of 7` and exited 0 under `CI_RUNNER_OS_LABEL=on`: the incident this block exists to
surface, reported green.
The EXIT is decided per CLASS, not "any zero-answer label":

- a bare `ubuntu-<version>` with **no** answer is verdicted **only when this pool is asked to serve
  it** — the per-label line prints `⛔ <label> = 0 of <m> online self-hosted runner(s) answer it.` and the
  block-level remedy then prints `⛔ UNCOVERED label(s) above`, with exit `5` (a verdict, and it is
  bad). "Asked" is read from `CI_RUNNER_OS_LABEL` through the installer's own `resolve_os_label`, so
  `on` — and `auto` on a matching host — asks, while `off` (the shipped default, and `auto`
  declining) does not.
  ⛔ **THAT READ IS THIS SHELL'S INTENT, NOT THE POOL'S — so it can only ADD a verdict, never
  subtract one.** The bare `--status` this section tells you to verify with has the variable unset
  (⇒ `off`), and a shell variable cannot say what the last installer pass, the autoscaler timer, or
  a peer lane asked for. Two consequences, both of which this block now states instead of papering
  over:
  - **`n == 0` with the flag off cannot be NAMED.** It is consistent with BOTH "the #6897 pin
    applied correctly" (nothing to do) and "asked and gone" — the pool eroded to zero one fill at a
    time, which is the incident state. The block reports the measurement plus both readings, exit
    `0`. It used to assert the harmless one (`NOT the incident state`), which cleared exactly the
    erosion this block exists to surface — a fail-open in the blocking direction, in the default
    invocation. To get the verdict anyway, run `CI_RUNNER_OS_LABEL=on --status` (read-only).
  - **`n > 0` OUTVOTES THE FLAG.** A self-hosted runner's labels are explicitly assigned — GitHub's
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
the pool is still asked (re-apply). `--repair-labels` cannot settle it either, because the version
pin is a NAMED carve-out of its union and that carve-out is this flag's `off` state: run it with the
variable unset and it removes the pin from the **last** carriers, run it with `on` and it re-applies
them. The block says so out loud and leaves the choice to you.

**Settling which one it is.** The pool's recorded intent is the environment the *scheduled* pass
runs with, not your shell's: `grep -n CI_RUNNER_OS_LABEL /etc/systemd/system/runner-autoscale.service`
(the systemd path; `SYSTEMD_UNIT_DIR` if you installed elsewhere) or the `CI_RUNNER_OS_LABEL` key in
`$HOME/Library/LaunchAgents/*runner-autoscale*.plist` on macOS. That line is rendered by
`tools/install-runner-autoscale-schedule.sh` from the mode the fill leg will actually use, and it is
rendered **unconditionally** — a provisioner plays no part in whether it is recorded, because the
same variable also decides the ORPHANED re-registration on the REPAIR leg, which needs none. A unit
with no such line is a unit installed before that pin existed; re-run the schedule installer.

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
stickier than it looks, too: a hand-written `ubuntu-latest` is a label GitHub has no record of
detaching for you, `--repair-labels` only ever ADDS (its two named carve-outs are the only removals
it performs), and a runner nobody re-registers keeps whatever it was given — so a mistake made there
outlives every pass that is supposed to correct it. `_preserve_labels` is the one path that re-derives
a set, and it DROPS the whole `ubuntu-<version>` class on the way through.

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
| Labels | `self-hosted`, `Linux`, `X64` (host-derived) + `self-hosted-x64` (custom, this arch's own) + `ubuntu-latest` while the fallback is engaged + `ubuntu-24.04` **only** when an operator opts in with `CI_RUNNER_OS_LABEL=on\|auto` (default `off`; the live pool is opted in as the temporary mitigation described in the label contract above) |
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

- `--count N` → `tools/install-ci-runner-x64.sh:1378` (the scale loop) · the two mode blocks the
  paragraphs below cite: `install` `:1200` (arch refusal dies at `:1205`) and `uninstall` `:1253` ·
  guards at `:1200-1251` (forks `:1208`, disk `:1228`, cpu/memory `:1235`) · sha256 pin +
  registration at `:1389-1399` (verify `:1389`, token `:1392`, one argv `:1397`, `config.sh`
  `:1399`; the run at `:1381` is the dry-run echo) · systemd install/start at `:1409-1411` · label
  resolution at `:272-373` (`fallback_engaged` `:276`, `resolve_os_label` `:304`, `resolve_labels`
  `:336`, whose body ends at its `}` on `:373`) · the union's label algebra at `:375-416` and the
  repair that uses it, `repair` `:992` · the label-coverage block `:450-970` (its header `say` at
  `:450`, its three no-verdict arms at `:735`, `:743` and `:752`, the uncovered verdict at `:970`)
  and the `--status` block that contains it `:427-972` · `--print-labels` `:1173`.
- `--count` is validated ONCE, before the mode dispatch, for EVERY mode (`:235`): a `--count 0
  --uninstall` is a usage error (exit 1), not a silent no-op that reports success having deregistered
  nothing. `CI_RUNNER_OS_LABEL` is validated in the same place (`:299-302`), so a typo is refused in
  `--status` and `--uninstall` too — not only in the three modes that resolve labels.
- **`--repair-labels` UNIONS — it can ADD a label, and the ONLY labels it removes are the two named
  carve-outs below; never `ubuntu-latest` or a custom label a workflow targets** (`:992-1162`). The
  absolute form of that sentence was wrong one bullet later — `test-slow` and `workflow-lint` name
  the version pin, and the version-pin carve-out removes it — so the carve-outs are named here as the
  removals they are. It reads the runner's CURRENT labels and PUTs `current ∪ computed`, because the old
  body was the computed set alone, i.e. a REPLACE: with the ARM64 pool stripped of `ubuntu-latest`
  (#6897) `--fallback auto` computes `self-hosted-x64` alone, so a repair removed the label the
  REQUIRED `python-ci-gate` aggregate is pinned to and the run sat `pending` with ZERO materialised
  jobs — no red, no green, every PR blocked (measured 2026-10-27: runs 40898791192 / 40901844458,
  `jobs=0`; a hand-restored label was gone within ~25 minutes, twice). Only the host-derived labels
  GitHub owns (`self-hosted`, `Linux`/`macOS`/`Windows`, `X64`/`ARM64`/`ARM` — refused by the API with
  HTTP 422) are left out of the body, and the before/after sets plus the derived `removed=[…]` list
  are printed BEFORE the PUT, so a removal would be visible in the output instead of inferred from a
  queue that never moves. An unreadable current label set is a REFUSAL (`:1017`) — never "use
  the computed set", which is the same strip through a different door — but it refuses THAT RUNNER,
  not the batch: it reports exit 3 and continues, because with `--count 2` an earlier runner's PUT
  may already have succeeded and exit 2 promises "nothing was changed" (`:109-136`). The same holds
  for the loop's illegal-label and empty-union guards.
  ⛔ **OVERRIDES:** `PUT …/runners/<id>/labels` is GitHub's **Set custom labels** verb, whose own
  description is "Remove all previous custom labels and set the new custom labels" — a REPLACE of
  the runner's custom set, and exactly what `resolve_labels` alone computed here. A repair PUTs
  `current ∪ computed` instead, because a replace cannot tell "this label is not wanted" from "a
  workflow targets it": it stripped `ubuntu-latest` from the only pool answering the REQUIRED
  `python-ci-gate` aggregate (runs 40898791192 / 40901844458 — `jobs=0`, every PR blocked). The only
  removals left are the two named carve-outs below, and both are PRINTED.
  ⛔ **`relabelled …` IS A CLAIM ABOUT THE SERVER, NOT ABOUT THE PUT'S EXIT STATUS** (`:1141`): the
  Set verb's own 200 carries the runner's resulting label set, and every label the repair asked for
  must be in it. A pool has already reported `set_labels ok=16 fail=0` for a label that was never
  attached (#6897), so a write that is accepted but not reflected is exit 3 with the returned set in
  the warning — never a `relabelled` line over a state nobody looked at.
  ⛔ **`--repair-labels --fallback off` is REFUSED (exit 2, before any read or PUT)** (`:992-1001`):
  a union cannot DETACH `ubuntu-latest`, so that invocation used to be accepted, print `removed=[]`,
  say `relabelled …` and exit 0 while the label it was asked to remove stayed attached. Detaching is
  the gate's writer; by hand it is `DELETE repos/<repo>/actions/runners/<id>/labels/ubuntu-latest`.
- **⚠️ TWO NAMED CARVE-OUTS from that union, and neither is the fallback label** (`:399-416`).
  `foreign-arch` is ALWAYS dropped; the version pin only under `CI_RUNNER_OS_LABEL=off`/unset, so
  under `on|auto` **the one spelling this box may claim (`ubuntu-24.04`) joins the union and the rest
  of the family stays dropped** (printed `version-pinned, kept=[…]`, see sub-bullet 1). Both are
  PRINTED, never silently skipped — keeping either makes a false PASS permanent — and
  `_preserve_labels` drops both on re-registration, reading NO flag. The WRITERS that can put the
  class back are the repair above and `tools/install-ci-runner-x64.sh::resolve_os_label` (`:304`) on
  the install side. ⛔ WHICH STATE A BOX IS IN CANNOT BE READ OFF THIS PAGE — the flag is an
  environment value on the box, so `systemctl show -p Environment runner-autoscale.service` (or just
  `env`) is the measurement, and the LIVE label set is read back with
  `gh api …/actions/runners`, never off this page.
  1. **`ubuntu-<version>`** (`ubuntu-24.04`, `ubuntu-22.04-arm`, …): the label a leg pins to ESCAPE
     this pool. Preserving it would un-pin `test-slow` / `workflow-lint` / `finding-provenance` /
     `mirror-images` onto a bare box whose `PATH` lacks their tools — the measured 2026-10-24
     failures (exit 127, `gh: command not found`). `ubuntu-latest` is NOT in the class (no version)
     and is always kept, as is a custom label that merely starts `ubuntu-<digits>` (the boundary
     case is `ubuntu-2204-tools`).
     ⛔ **THIS CARVE-OUT IS THE `off` STATE OF A FLAG, NOT AN UNCONDITIONAL DROP.** It drops the pin
     when `CI_RUNNER_OS_LABEL` is `off`/unset and KEEPS it under `on|auto` (printed
     `version-pinned, kept=[…]`). Export `CI_RUNNER_OS_LABEL=on` to keep the pin; the removal is
     printed as `version-pinned, not kept=[…]` / `removed=[…]` before the PUT. An unrecognised value
     never reaches this carve-out at all — the flag is validated once, before the mode dispatch.
     ⛔ **AND THE KEEP IS NARROWED TO THE LABEL THIS BOX CAN CLAIM, NOT THE WHOLE CLASS.** `on|auto`
     keeps `ubuntu-24.04` — the only spelling `resolve_os_label` can emit — and nothing else in the
     family: a label names an IMAGE, so a repair that kept `ubuntu-24.04-arm` would let an x86_64
     runner serve an ARM64 job and make a false PASS permanent (a repair UNIONS, so a kept pin
     survives until an explicit `off` repair). `ubuntu-22.04`, `ubuntu-24.04-arm` and every other
     version pin are printed on `version-pinned, not kept=[…]` / `removed=[…]` with a
     `NOT KEPT: '<label>' …` line naming the reason — the same class `_preserve_labels` drops on
     re-registration, on every arch.
  2. **A foreign arch marker** (`self-hosted-*` other than `self-hosted-x64`): the pre-#2327 stamp
     this repair is supposed to HEAL. Keeping `self-hosted-arm64` on an x86 box makes an
     arch-specific job routable to it and produces a wrong artifact.
     ⛔ **AND THE KEEP-HALF IS ARCH-AWARE, which is what stops the union re-creating #6897's own
     defect.** A repair may run from any host against any install; if the runner's READ-ONLY host
     label says `ARM64`/`ARM` it is refused (exit 3, nothing written) instead of being PUT
     `self-hosted-x64` and KEPT on `ubuntu-latest` — that keep is exactly the measured false red (an
     arm64 box wins an `ubuntu-latest` job and dies in "Set up job", one shard per run). The
     host-derived label is the one witness a caller cannot talk the script out of.
- **`--status` answers "who answers this `runs-on:` label"** (`:450-970`): per label the workflows
  name, it counts the ONLINE self-hosted runners that CAN answer it (the sub-pool for that label's
  architecture, so a two-arch pool is not misread as erosion), prints `N of M online runner(s)` —
  and only ONLINE carriers count, with the offline ones printed as context — and reports
  any label with no self-hosted answer. A label nobody answers does not fail — it does not run: the
  run stays `pending` with zero jobs forever, so the queue has to be READ. The label set is parsed
  from BOTH `.github/workflows/*.yml` and `*.yaml`, in every documented `runs-on:` form (a scalar, a
  flow array in either quoting, a block sequence, and a `group:`/`labels:` mapping — a runner GROUP
  is consumed, never counted as a label), and a `runs-on:` it cannot read as a STATIC label set is
  refused (`:743`), never cleared: a partial read is not a coverage verdict. It reads nothing at all
  → `:735`; it parses the files but finds no label to check → `:752`. All three are exit **4**.
  ⛔ **`0` is not a verdict on its own.** A zero answer is the incident ONLY when this pool is ASKED
  to serve that label — read from `CI_RUNNER_OS_LABEL` in the invoking shell, which is why the
  intent read can only ADD a verdict, never subtract one: `n > 0` (a measured carrier) outvotes a
  shell, because a runner's labels are explicitly assigned, and the intent is per LABEL
  (`resolve_os_label` can only ever set `ubuntu-24.04`, so another bare pin is a report even under
  `on`). Under the shipped default (`off`) a zero-answer `ubuntu-24.04` exits **0** and prints BOTH
  readings — #6897's pin applied correctly, or erosion to zero — because one shell cannot tell them
  apart; `ubuntu-latest` at zero is likewise a report (that is the healthy state). `0 < n < M` is
  `⚠️ PARTIAL`, and its remedy is class-correct: only the bare pin is re-applied by this script.
  Exit **5** when the pool IS asked for a required label and answers with none (`:970`).
- **`--uninstall` deletes only what it can PROVE is a runner install, and only after its unit is
  gone** (`:1252-1361`; the proof is `is_runner_install` at `:268`, which accepts `.runner` or  `bin/Runner.Listener` — a lone `config.sh` is NOT proof): one of those must exist in the directory, every target is validated BEFORE the first
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
- **Exit codes** (`:109-136`; `--help` prints the same block): `0` ok · `1` usage · `2` guard
  refused — headroom, arch, an undecidable fallback, an explicit `--repair-labels --fallback off`
  (the repair only unions, so it cannot detach the fallback label), or an uninstall target that is
  not provably a runner, with nothing downloaded or registered,
  so re-run with a smaller `--count` · `3`
  operation failed — download, sha256 verify, extract, token mint, `config.sh`, `svc.sh` (including
  a unit removal that failed, whose target directory is then left intact, or a deregistration that
  failed, likewise left intact), a tool missing from PATH, a failed remote read, **or a per-runner
  refusal inside the repair loop** (an unreadable label set, an illegal label, an empty union): that
  runner is skipped and the rest of the batch still runs, so the code cannot be 2 · `4`
  label-coverage could not read the workflows, so it has no verdict — no
  `.github/workflows/*.yml|*.yaml` where `--status` was run, workflows that name no
  single-label `runs-on:` at all, or a `runs-on:` whose labels are not a static label set (a
  `${{ … }}` expression in the value OR in any single member of a list, an unquoted spelling this
  parser cannot carry as one label, a block-scalar indicator). Nothing was read, so nothing was
  cleared — and on a partial read the block prints NO per-label verdict and NO remedy, because a `4`
  that follows a `⛔ UNCOVERED` line contradicts itself: re-run `--status` from the directory holding
  `.github/workflows`, because an empty read is not a coverage verdict — and neither is a PARTIAL
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
  arch refusal still governs every INSTALL — and only an install (`:1200`, the `MODE = install`
  block; the refusal itself dies at `:1205`). `--status`, `--repair-labels` and `--print-labels` are
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
   removes — `labels_for` `:218-233`). `tools/install-ci-runner-x64.sh --repair-labels` re-applies
   this arch's marker and, on an engaged pool, **adds** `ubuntu-latest` back — it is ADD-ONLY, so it
   cannot detach the label and `--repair-labels --fallback off` is REFUSED (exit 2, with the
   `DELETE …/labels/ubuntu-latest` call named in the refusal). Detaching is the gate's own writer
   below (`fallback_off`, agent-infra); the repair is the additive half.

   ⛔ **WHAT THE UNION DOES AND DOES NOT BUY, stated plainly so the next stall is not read as
   closed.** The repair hardening fixed the *manual* and *provisioner-adjacent* path only: a repair
   can no longer strip `ubuntu-latest`, and nothing else in this repo calls `--repair-labels`
   (`tools/runner_autoscale.py:1061` invokes the installer as `--count … --fallback auto`, never
   `--repair-labels`, and its own `_preserve_labels` already kept the label). It does NOT stop the
   remover above: the gate's `fallback_off` still strips `ubuntu-latest` from this pool on its next
   tick, from `scripts/ci_runner_gate.sh` in **agent-infra**, which is not in this checkout. So the
   label can still disappear within one tick after a hand repair — the gate-side removal is its own
   follow-up, tracked by open **#10237** — and re-measuring (`--status`, then `gh api …/runners`) is
   still the way to know.
2. **The pool is still the slower class for long legs.** These runners are not pinned off the
   fast matrix (`test (a)…(i)`), so a 20m watchdog leg can still be killed here if this box
   turns out slower than hosted under load. That is the #6897 measurement, unchanged — and while hosted
   is unavailable the version-pinned legs are exactly the ones this pool now has to serve, at that
   measured risk. The rest of the fast matrix is unaffected.
3. **`ubuntu-latest` remains ambiguous by design.** Any job asking for it can land on
   GitHub-hosted, an ARM64 runner, or one of these — that ambiguity is what the fallback is
   for, and why the legs that need a specific class are version-pinned.
4. **The OPT-IN split (`CI_SHARD_ROUTING=pool-split`) makes the pool a PIN on 4 of 9 required legs,
   and that exposure is deliberate but uncomfortable.** It is off by default and it is recorded in
   its own section, with an `OVERRIDES:` line (`→ The fast-matrix split is OPT-IN`). In one line:
   under `pool-split`, `test (b)`, `(d)`, `(f)` and `(h)` name `[self-hosted, self-hosted-x64]`, so a
   pool outage or a contended box holds those required checks **pending** rather than answering them
   from GitHub-hosted — the exact trade the "any POOL-ONLY label" rule refuses, taken
   deliberately for those four rows and nowhere else.

## The box's own choice: `CI_RUNNER_OS_LABEL`

One flag decides whether a box claims a version-pinned hosted label (`ubuntu-24.04`). **Both sides
now live in this tree and read the same three values.** The WRITERS: `resolve_os_label` in
`tools/install-ci-runner-x64.sh` (`:304`), reached from `resolve_labels` — so install, repair and
`--print-labels` all emit it — plus `runner_autoscale.py::_os_labels` for the ORPHANED
re-registration path, and `tools/install-runner-autoscale-schedule.sh`, which renders the value into
the timer so a scheduled fill inherits the box's choice. The READER that decides the version-pin
carve-out is the `case` in the repair block. An unexported shell therefore still takes the `off` row
below — which is #6897's recorded revert, not a no-op. ⛔ WHICH ROW IS LIVE IS A MEASUREMENT, not a
fact this page holds (see *Re-measure, never assume*): a box whose unit carries `on` KEEPS the pin,
and `systemctl show -p Environment runner-autoscale.service` is what says which it is.

| `CI_RUNNER_OS_LABEL` | the box claims `ubuntu-24.04` | `--repair-labels` on a runner that carries it |
| --- | --- | --- |
| `off`, or unset | no — the pre-departure invariant | DROPS it, prints `version-pinned, not kept=[…]` |
| `on` | yes | KEEPS it — and ONLY it (`ubuntu-24.04-arm` etc. stay dropped) — prints `version-pinned, kept=[…]` |
| `auto` | yes, derived from `/etc/os-release` | KEEPS it and only it (the box may claim one) |
| anything else | REFUSED before any mode runs (exit 1) | — nothing is read, and nothing is PUT |

- **Why the repair has to read it.** #6897's `OVERRIDES:` marker suspends the version-pin invariant
  and records the revert as `CI_RUNNER_OS_LABEL=off` **plus** `--repair-labels`. The repair honours
  that switch, and the honest consequence is that an UNSET flag IS `off` — which is what a hand-run
  command has, since the only WRITER is the SCHEDULE (`tools/install-runner-autoscale-schedule.sh`,
  which renders the value into the timer; a manual invocation inherits no such environment). So the
  next manual heal on the live pool — measured 2026-10-29, 7 of 7 x64 runners carrying
  `ubuntu-24.04` — performs that revert unless the operator exports `CI_RUNNER_OS_LABEL=on` first. It is a PRINTED removal (`version-pinned, not kept=[…]`,
  `removed=[…]`, both before the PUT), never a silent one; and a repair that ignored the flag would
  make the recorded revert path unreachable instead.
- **What the UNION does with it.** `resolve_labels` appends `$OS_LABELS` on EVERY arm, after its
  `case` (`LABELS="$LABELS$OS_LABELS"` — the one append point), so
  under `on|auto` the computed set CARRIES `ubuntu-24.04`: the repair therefore ADDS it to a runner
  that lacks it, not merely preserves an attached one. Under `off`/unset the same carve-out removes
  it. The `kept=` / `not kept=` lines say which half ran, and `--print-labels` prints the set before
  anything is PUT.
- **The one place this rests on the repair.** `tools/runner_autoscale.py::_preserve_labels` DROPS
  every `ubuntu-<version>`, including the bare spelling, so a runner RE-registered by the scheduled
  pass loses a pin it legitimately carried (`docs/ops/ci-runner-x64.md:25-26` is the invariant it
  restores). That is a NAMED residual, not an oversight: the function's docstring records it, and
  nothing there re-attaches the label — `--repair-labels` does. `--status`' `label-coverage` exit 5
  is what makes the gap visible instead of silent, and it is why a pin you meant to KEEP is removed
  with `CI_RUNNER_OS_LABEL=off --repair-labels`, never by a bare repair.
- **Re-measure, never assume.** The state is per-box and the fleet has moved twice in two days:
  `systemctl show -p Environment runner-autoscale.service` (the systemd path — an empty answer means
  the schedule was installed with the flag unset, i.e. the `off` row), then
  `gh api repos/<owner>/<repo>/actions/runners --jq '.runners[]|select(.name|startswith("pi-runner-x64"))|{name,labels:[.labels[].name]}'`.

## The fast-matrix split is OPT-IN (`CI_SHARD_ROUTING=pool-split`)

**Decision relay (PR #10186, 2026-10-30).** #6897's ruling is *use the compute capacity wherever
it is, throughput first*. An earlier cut of #10186 read that as *route 4 of 9 gated fast shards to
the pool by default* — which is an adoption over the `fallback-only` decision above, and not a
lane's to take. It was rejected on measurement, not on taste:

| run | what ran | result |
| --- | --- | --- |
| `40981781885` (`main`, plain `ubuntu-latest`) | pool won 5 of 9 legs by the fallback race | **5 of 5 pool legs killed**; 4 of 4 hosted legs passed in 10-12 m of a 20 m budget |
| `40986603502` (head `5a2ae6aa1`, split ON) | odd → `self-hosted-x64`, even/canary → `ubuntu-latest` | 3 of 4 pool legs failed — two on `redis ConnectionError 111 localhost:6379`, not on the budget; `test (i)` (**the canary**) was taken by `pi-runner-vm-5` (ARM64) and killed at 32 m 12 s |

So the split ships **available and off**: `tools/ci_selection.py::resolve_shard_routing()` is the one
decision point, `CI_SHARD_ROUTING=pool-split` opts in, and **unset / empty / `hosted-only` / any
other value resolves to hosted-only** — every fast row back on `ubuntu-latest`, exactly what main
does. A typo or a fork with no `vars` therefore fails SAFE, never onto the contended class.
`python-ci.yml`'s `test` job takes its class from `matrix.runner` only, so there is no second copy
of the mode to drift.

The lever is carried by **both emitter STEPS** on `python-ci.yml`'s `changes` job — `push-matrix`
(`--emit-push-matrix`, every event) and `split` (`--split`, tier-2 PRs, where its output OVERRIDES
the push matrix) — and by nothing else. Scoped to ONE step the variable is silently absent on the
other and the PR surface never splits (measured on head `3db8f7d78`, where only the push step
carried it); `tests/test_ci_selection.py::test_the_opt_in_reaches_every_matrix_emitter_in_the_changes_job`
discovers the emitters from their `run:` commands, so a third one cannot miss the lever either.

⛔ **Not job scope, and that is a pin, not a preference.** An earlier cut of this lever sat on the
`changes` JOB; that job also owns the capstone pre-registration gate step, and
`tests/test_capstone_preregistration_9075.py` allow-lists the `env:` keys above that step with an
EMPTY job allow-list (any key there can hook the gate's interpreter — `BASH_ENV`, a `PYTHONPATH`
`sitecustomize.py`, `LD_PRELOAD`, `PATH` — so `python3` exits 0 with the real check never running).
Measured at head `3eaf43fda`, job scope returned one `ci_ordering_half_violations` finding and red
a REQUIRED fast shard (run `41002293616`, `test (e)`). `tests/test_ci_selection.py::test_the_changes_job_carries_no_job_scope_env`
now forbids the job-scope form, so "put it back on the job" is a red check, not a simplification.

### Residual: under `pool-split` the pool is a PIN on 4 of 9 required legs

**OVERRIDES:** the "any POOL-ONLY label" rule recorded above — `pool-split` names
`[self-hosted, self-hosted-x64]` on the odd rows (`test (b)`, `(d)`, `(f)`, `(h)`), because a mode
whose odd rows still raced for a hosted label would not route at all; the departure is OPT-IN, off by
default, and its cost is stated here rather than inherited silently.

Label matching is cumulative — a runner must carry EVERY label named in `runs-on` — so the pair
`[self-hosted, self-hosted-x64]` matches only these x64 runners, which carry both; a bare
`self-hosted-x64` would select them too. What that costs is exactly the trade the rule refuses:

- **No hosted half on those four legs.** With the gate in `fallback_off` (the normal state on a
  healthy day), or the box simply down, a `self-hosted` pin does not degrade to anything — it
  WAITS. `test (b)`, `(d)`, `(f)`, `(h)` sit `queued`/`pending` with no GitHub-hosted runner able to
  answer them, so `python-ci-gate` stays pending and the PR does not merge until the pool is back.
  This is what turns "the pool is slower" (residual 2 above) into "the gate is blocked": measured
  2026-10-27..30, three pool legs were killed at their own watchdogs while hosted legs finished in
  10-12 m of a 20 m budget (the table above).
- **The even rows keep the opposite residual.** `ubuntu-latest` is answered by GitHub-hosted *or* by
  the pool while the gate carries the fallback label, so a pool runner can take an even shard:
  `test (i)` — the canary — was taken by `pi-runner-vm-5` (ARM64) and killed at 32 m 12 s in run
  `40986603502`. Neither half is class-safe in either mode; that is why the split is off.

Changing the DEFAULT is a decision, and its home is #6897: it needs idle pool capacity at schedule
time **and** a pool job wall within ~1.2x hosted on these rows, neither of which the runs above
show. Do not set `pool-split` to "try it out" — a killed leg is a required check.
