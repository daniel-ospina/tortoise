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
count each job's `runs-on`): **77 / 9**, which is exactly what `--status`'s `label-coverage` block
now prints — it used to read 85 / 12 because its extractor counted `runs-on:` mentions in YAML
**comments** (it strips whole-line comments first now). Only `runs-on:` lines that name a single
`ubuntu-*` label are counted; the `ubuntu-latest` row was previously written as 83 against a measured
79, so treat both rows as a dated measurement and re-derive them rather than trusting the numbers.

The architecture labels are **derived by GitHub from the host at registration** and cannot be
set by hand: the ARM64 VMs register `self-hosted, Linux, ARM64, self-hosted-arm64, ubuntu-latest`,
and this box registers `self-hosted, Linux, X64, self-hosted-x64, ubuntu-latest`. So:

- `ARM64`/`X64` are **ornaments** — no job matches on them, and none can (a `runs-on` naming
  either would have to name `self-hosted` too, which the repo forbids: `.github/workflows/python-ci.yml:1556`
  is "Do NOT change it back to `ubuntu-latest`, and never to `self-hosted`", and the reason is
  recorded at `:1544-1546` — a `self-hosted` pin removes the GitHub-hosted half of the race, so
  during a hosted outage the required leg WAITS and "the aggregate stays pending — not answered
  yet" instead of being answered by the pool).
- `ubuntu-latest` is a **custom label** on the pool. `scripts/ci_runner_gate.sh:289`
  (`fallback_on`) attaches it, `:290` (`fallback_off`) detaches it.
- The ARM64-ness is an accident of the first runner's hardware (OrbStack VMs on an
  Apple-Silicon Mac — `mirror-images.yml:92` records `orbctl run -m pi-runner uname -m` →
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
until the last carrier was replaced. Two pins close it: `tools/install-runner-autoscale-schedule.sh`
renders `CI_RUNNER_OS_LABEL` into the timer (systemd unit, launchd plist, cron entry) **and** its
probe, captured from the operator's environment at install time and defaulting to `off`, so the
install command is the opt-in:

```bash
CI_RUNNER_OS_LABEL=on tools/install-runner-autoscale-schedule.sh   # the FILL leg then attaches it
```

and `--status` now reports `⚠️ PARTIAL` for any label only part of the pool answers, which is the
read that sees the drift before it reaches zero. **Restore after a drift**, on each affected box:

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

**The decision this forces, stated as a decision.** Serving those legs from this pool costs what
#6897 measured — the pool is ~2x slower, so a 20m watchdog leg can be KILLED here that hosted
finished, i.e. a **red on healthy code**. Leaving them pinned costs a gate that is **never
dispatched**: no red, no green, and **no merges at all**. On 2026-10-28 the second cost was the one
being paid, so the pool carries `ubuntu-24.04` as a temporary, explicitly-flagged mitigation — and the
installer's default stays `off`, so a fresh box does not silently reverse #6897. Flipping the default
is the one-line change if the owner prefers the other trade; reverting the pool is
`CI_RUNNER_OS_LABEL=off` plus a label reset.

⛔ **The mitigation is label-WIDE, and two of the legs it now makes servable are pinned for reasons
that are NOT speed.** A label cannot be scoped to "the legs it starves": the moment the pool carries
`ubuntu-24.04`, all **nine** `runs-on: ubuntu-24.04` jobs become eligible for it. Two of those pins
exist to keep the job OFF the fleet, and neither is about the watchdog:

- `.github/workflows/mirror-images.yml` — *"a hosted runner has its own egress IP, so these pulls
  cannot spend the quota the self-hosted fleet shares; running it on the fleet would spend the very
  quota it exists to stop spending."* The pool paying this cost gets **no** deadlock relief in
  return: `mirror-images` is a nightly cron/`workflow_dispatch` job, starved by nothing.
- `.github/workflows/python-ci.yml` (`workflow-lint`) — its comment reasons that
  *"`ubuntu-24.04` is claimed by no self-hosted runner"*, which this mitigation makes **false**. The
  leg pulls `rhysd/actionlint` by a single literal reference, so on the pool it spends the fleet's
  shared anonymous Docker Hub quota — a rate-limit red on healthy code, a failure class the #6897
  analysis does not cover.

These are **accepted costs of the temporary mitigation, stated as costs**: cheap next to an
undispatched required aggregate, and both revert with the mitigation. If the mitigation becomes
permanent, the scoping fix is to put the starved required legs on a label the pool answers and leave
`mirror-images` / `workflow-lint` on one only hosted answers.

**The escape hatch, and the proof it works.** `ubuntu-24.04` is an **OS fact**, not a fallback label:
`CI_RUNNER_OS_LABEL=on` (or `=auto`, which derives it from `/etc/os-release`) rides it on every label
arm, so `--fallback off` cannot drop it. The 8-hour-stuck gate **dispatched within a minute** of the
label being added, on `pi-runner-cmux-x64-6`, and completed in 6 s.

**Verify it — do not assume it.** `tools/install-ci-runner-x64.sh --status` now ends with a
`label-coverage` block: it reads every `runs-on:` label out of the workflows and prints how many
runners answer it, marking any zero-answer label `⛔ UNCOVERED` (exit `5` — a verdict, and it is
bad) and any label only PART of the pool answers `⚠️ PARTIAL`. Run it from the repo root. A fresh
box, a re-provisioned pool, or a new workflow with a new `runs-on:` value all show up there instead
of as an unexplained all-PR block — and so does the erosion below, **before** the last carrier of a
label is replaced: `3 of 12 runners answer it` is the shape that reaches zero answers one fill at a
time with nothing red in between. One read is one `--paginate`d pool listing, so a pool larger than
`per_page` cannot read as uncovered.

**Adding capacity (the second half).** One runner instance = one concurrent job, so extra instances
are the only lever on throughput besides trimming the matrix:

```bash
# on the box, reusing an existing install; registration token from the repo API
sudo cp -a /opt/gh-runner-x64-4 /opt/gh-runner-x64-5
sudo rm -rf /opt/gh-runner-x64-5/{.runner,.runner_migrated,.credentials,.credentials_rsaparams,.service,.env,.path,_work,_diag}
sudo chown -R "$(id -un):$(id -gn)" /opt/gh-runner-x64-5
cd /opt/gh-runner-x64-5 && ./config.sh --unattended --replace --url https://github.com/<owner>/<repo> \
  --token "$TOK" --name <new-name> --labels self-hosted-x64,ubuntu-latest,ubuntu-24.04 --work "$PWD/_work"
sudo ./svc.sh install "$(id -un)" && sudo ./svc.sh start     # NOT nohup: a reboot must not lose it
```

⛔ **`.runner_migrated` counts as "already configured"** — a copied directory that keeps it (or
`.runner`) makes `config.sh` refuse with *"Cannot configure the runner because it is already
configured"*. Remove every one of the files listed above, not just `.runner`.
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

- `--count N` → `tools/install-ci-runner-x64.sh:658` (the scale loop) · the two mode blocks the
  paragraphs below cite: `install` `:480` (arch refusal dies at `:485`) and `uninstall` `:533` ·
  guards at `:480-530` (forks `:488`, disk `:508`, cpu/memory `:515` — `:528`, the memory guard,
  is the last one) · sha256 pin + registration at `:669-680` (verify `:669`, token `:672`, one
  argv `:678-679`, `config.sh` `:680`; the run at `:661` is the dry-run echo) · systemd
  install/start at `:689-691` · label resolution at `:216-309` (`fallback_engaged` `:216`,
  `resolve_os_label` `:244`, `resolve_labels` `:272`, whose body ends at its `}` on `:309`) ·
  `repair` `:420` · `--print-labels` `:453` · label-coverage at `:334-413` (the two "read
  nothing" arms at `:396` and `:405`, the uncovered verdict at `:413`).
- `--count` is validated ONCE, before the mode dispatch, for EVERY mode (`:180-184`, the `case`
  through its `esac`): a `--count 0 --uninstall` is a usage error (exit 1), not a silent no-op
  that reports success having deregistered nothing. `CI_RUNNER_OS_LABEL` is validated the same
  way and in the same place (`:239-242`), so a typo is refused in `--status` and `--uninstall`
  too — not only in the three modes that resolve labels.
- **`--uninstall` deletes only what it can PROVE is a runner install, and only after its unit is
  gone** (`:452-560`; the proof is `is_runner_install` at `:199`, which accepts `.runner` or
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
- **Exit codes** (`:65-80`; `--help` prints the same block): `0` ok · `1` usage · `2` guard
  refused — headroom, arch, an undecidable fallback, or an uninstall target that is not provably
  a runner, with nothing downloaded or registered, so re-run with a smaller `--count` · `3`
  operation failed — download, sha256 verify, extract, token mint, `config.sh`, `svc.sh` (including
  a unit removal that failed, whose target directory is then left intact, or a deregistration that
  failed, likewise left intact), a tool missing from PATH, or a failed remote read · `4`
  label-coverage could not read the workflows, so it has no verdict — no
  `.github/workflows/*.yml|*.yaml` where `--status` was run, or workflows that name no
  single-label `runs-on:` at all. Nothing was read, so nothing was cleared: re-run `--status`
  from the repo root, because an empty read is not a coverage verdict · `5` label-coverage WAS
  read and found a required label with **no** self-hosted answer — the incident state, i.e. a
  verdict and it is bad. **`0` does not mean "covered":** `0` is what a report with no ⛔ line
  exits with, and a `0`-runner label inside a real read is the `5` case, not a clean bill of
  health. Nothing in the tree keys on `--status`'s status today, so this is a signal for an
  operator or a future `--status && …` guard, not a change any caller currently reads.
- `--print-labels` answers BEFORE the arch and headroom guards, so the label invariant is
  checkable on any host (including the Apple-Silicon dev box and the ARM64 pool runners); the
  arch refusal still governs every INSTALL — and only an install (`:480`, the `MODE = install`
  block; the refusal itself dies at `:485`). `--status`, `--repair-labels` and `--print-labels` are
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
