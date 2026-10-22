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
| `ubuntu-latest` | 83 | GitHub-hosted **and** (while the fallback is engaged) the self-hosted pool |
| `ubuntu-24.04` | 5 | **GitHub-hosted only** — pinned on purpose (`test-slow`, `workflow-lint`, `finding-provenance` ×2, `mirror-images`) |

The architecture labels are **derived by GitHub from the host at registration** and cannot be
set by hand: the ARM64 VMs register `self-hosted, Linux, ARM64, self-hosted-arm64, ubuntu-latest`,
and this box registers `self-hosted, Linux, X64, self-hosted-x64, ubuntu-latest`. So:

- `ARM64`/`X64` are **ornaments** — no job matches on them, and none can (a `runs-on` naming
  either would have to name `self-hosted` too, which the repo forbids: that label removes the
  GitHub-hosted half of the race and re-opens #4732's "a skipped check reads as a pass").
- `ubuntu-latest` is a **custom label** on the pool. `scripts/ci_runner_gate.sh:184`
  (`fallback_on`) attaches it, `:185` (`fallback_off`) detaches it.
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
fallback — which the decision never specified. The separate decision that pins five legs to
`ubuntu-<version>` (the pool is ~2x slower and killed `test-slow` at its watchdog, #6897/#7867)
is untouched: these runners deliberately **do not** claim any `ubuntu-<version>` label.

## What is installed

| | |
| --- | --- |
| Runner names | `pi-runner-x64-1`, `pi-runner-x64-2` |
| Labels | `self-hosted`, `Linux`, `X64` (host-derived) + `self-hosted-x64` (custom, this arch's own) + `ubuntu-latest` while the fallback is engaged |
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
DISK_RESERVE_GB,MEM_PER_RUNNER_GB,MEM_RESERVE_GB,FORKS_PER_RUNNER,FORK_CAP,NCPU}`) so a bound can
be exercised on its own; a value that is present and unparseable is refused (exit 1), never
coerced to zero.

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

- `--count N` → `tools/install-ci-runner-x64.sh:395` (the scale loop) · guards at `:272-317`
  (forks `:275`, disk `:295`, cpu/memory `:302`) · sha256 pin + registration at `:401-414`
  (verify `:406`, token `:409`, one argv `:412-413`, `config.sh` `:414`) · systemd
  install/start at `:423-426` · label resolution at `:169-207` (`fallback_engaged` `:173`,
  `resolve_labels` `:184`) · label repair at `:226` · `--print-labels` at `:258`.
- `--count` is validated ONCE, before the mode dispatch, for EVERY mode (`:145-149`): a
  `--count 0 --uninstall` is a usage error (exit 1), not a silent no-op that reports success
  having deregistered nothing.
- **`--uninstall` deletes only what it can PROVE is a runner install, and only after its unit is
  gone** (`:320-379`; the proof is `is_runner_install` at `:165`): `.runner`, `config.sh` or
  `bin/Runner.Listener` must exist in the directory, every target is validated BEFORE the first
  deletion, and every removal token is minted BEFORE the first deletion — so a refusal in any of
  those phases leaves every directory untouched, never a half-uninstalled set. `CI_RUNNER_BASE` is
  operator input, so "a directory exists at this path" is not evidence of a runner — the earlier
  gate was exactly that, and `CI_RUNNER_BASE=/opt --uninstall` would have run `sudo rm -rf /opt`.
  A validation refusal is exit 2 and nothing is removed. A target whose systemd unit cannot be
  removed (`svc.sh uninstall` fails while the unit is still installed, or the unit is installed and
  the directory has no `svc.sh`) is **left intact, registration and all**, and reported: the unit
  would otherwise survive pointing at a deleted working directory while `--uninstall` exited 0.
- **Exit codes** (`:53-66`; `--help` prints the same block): `0` ok · `1` usage · `2` guard
  refused — headroom, arch, an undecidable fallback, or an uninstall target that is not provably
  a runner, with nothing downloaded or registered, so re-run with a smaller `--count` · `3`
  operation failed — download, sha256 verify, extract, token mint, `config.sh`, `svc.sh` (including
  a unit removal that failed, whose target directory is then left intact), a tool missing from
  PATH, or a failed remote read.
- `--print-labels` answers BEFORE the arch and headroom guards, so the label invariant is
  checkable on any host (including the Apple-Silicon dev box and the ARM64 pool runners); the
  arch refusal still governs every INSTALL.
- `--fallback auto|on|off` decides whether `ubuntu-latest` is attached (auto follows the ARM64
  pool). `--print-labels` answers "which labels would a runner get" without touching a guard,
  token or the network — that is the seam the label invariant is tested through. `--repair-labels`
  re-applies the label set to already-registered runners: the repair for residual 1, needing no
  re-download and no re-registration.
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

1. **`ci_runner_gate.sh` relabels every runner in the repo — NOT YET FIXED.** `set_labels`
   (`scripts/ci_runner_gate.sh:162`) enumerates *all* runner ids (`runner_ids` `:150`, no pool
   filter) and PUTs the ARM64 pool's label body (`fallback_on` `:184`: `{self-hosted-arm64,
   ubuntu-latest}`; `fallback_off` `:185`: `{self-hosted-arm64}`). On the pre-fix gate that means an
   x86 runner gets `self-hosted-arm64` stamped onto it — a false architecture claim a future
   arch-specific job could be routed into — and loses `self-hosted-x64`, so nothing distinguishes
   it from an ARM64 box in the API.

   ⛔ **The trigger is the next gate TICK, not the next transition.** The gate re-applies labels
   when `mode != want || prev != want` (`:274`), and `current_mode` (`:187`) counts *any* runner
   carrying `ubuntu-latest` as `local`. While these runners carry that label, the gate cannot read
   `github` — so on a day GitHub-hosted is healthy it runs `fallback_off`, which is the path that
   stamps the ARM64 label. Filed as **`daniel-ospina/agent-infra#2327`** (per-runner label sets:
   preserve each runner's own pool label, toggle only `ubuntu-latest`). Until it lands, repair with
   `tools/install-ci-runner-x64.sh --repair-labels` (re-applies this arch's marker and syncs the
   fallback label to the gate's state, no re-registration).
2. **The pool is still the slower class for long legs.** These runners are not pinned off the
   fast matrix (`test (a)…(i)`), so a 20m watchdog leg can still be killed here if this box
   turns out slower than hosted under load. That is the #6897 measurement, unchanged; the
   version-pinned hosted legs stay pinned.
3. **`ubuntu-latest` remains ambiguous by design.** Any job asking for it can land on
   GitHub-hosted, an ARM64 runner, or one of these — that ambiguity is what the fallback is
   for, and why the five legs that need a specific class are version-pinned.
