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
| `ubuntu-latest` | 75 | GitHub-hosted **and** (while the fallback is engaged) the self-hosted pool |
| `ubuntu-24.04` | 9 | **GitHub-hosted only** — pinned on purpose (`test-slow`, `workflow-lint`, `finding-provenance` ×2, `mirror-images`, and the four fleet observers `availability-liveness:check`, `availability-watchdog:probe`, `ci-check-repair:repair`, `ci-queue-health:queue-health`) |

Counts are JOBS (not files), measured 2026-10-27 at `a97a6b1` + the four-observer pin. Only `runs-on:`
lines that name a single `ubuntu-*` label are counted; the `ubuntu-latest` row was previously
written as 83 against a measured 79, so treat both rows as a dated measurement and re-derive them
(`yaml.safe_load` each workflow, count each job's `runs-on`) rather than trusting the numbers.

The architecture labels are **derived by GitHub from the host at registration** and cannot be
set by hand: the ARM64 VMs register `self-hosted, Linux, ARM64, self-hosted-arm64, ubuntu-latest`,
and this box registers `self-hosted, Linux, X64, self-hosted-x64, ubuntu-latest`. So:

- `ARM64`/`X64` are **ornaments in DEFAULT routing** — no job in the default tree requests them, so
  nothing distinguishes the classes today. They are NOT unroutable: labels match cumulatively and a
  custom label routes on its own (that is how `ubuntu-latest` reaches the pool, next bullet), so a
  bare `runs-on: self-hosted-x64` DOES select this box. What the repo refuses **as a default** is
  naming `self-hosted`: the `test-slow` pin's header in `.github/workflows/python-ci.yml` is "Do NOT
  change it back to `ubuntu-latest`, and never to `self-hosted`", and the reason is that a
  `self-hosted` pin removes the GitHub-hosted half of the race, so during a hosted outage the
  required leg WAITS and "the aggregate stays pending — not answered yet" instead of being answered
  by the pool. **One opt-in departure exists and is recorded below** — `CI_SHARD_ROUTING=pool-split`
  names `[self-hosted, self-hosted-x64]` on the fast matrix's ODD rows, with its `OVERRIDES:` line
  and its outage-wait exposure (`→ The fast-matrix split is OPT-IN`); read that section with this
  one, since what makes the pair exclusive is the ABSENCE of `ubuntu-latest`, not the label pair.
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
is untouched BY DEFAULT: these runners deliberately **do not** claim any `ubuntu-<version>` label
unless the box is OPTED IN — and the live pins were attached WITHOUT that switch (nothing in this
tree writes it yet; #10275 ships the writer): unset is the state to assume. Measure it, do not read
it off this sentence: on 2026-10-29 all 7 x64 runners carried `ubuntu-24.04` — the departure #6897's
`OVERRIDES:` marker records and measures as APPLIED. Its revert is `CI_RUNNER_OS_LABEL=off` + `--repair-labels`,
and unset IS `off`, so a plain repair IS that revert; export `CI_RUNNER_OS_LABEL=on` to keep a pin
(see *the box's own choice* below). Otherwise the class is the pin nothing here creates: the
installer emits one only under `CI_RUNNER_OS_LABEL=on|auto` (#10275 adds it), the arm64 spellings
(`ubuntu-24.04-arm`, `ubuntu-22.04-arm`, `ubuntu-26.04-arm`) are never emitted, and
`tools/runner_autoscale.py::_preserve_labels` DROPS one on re-registration, so an out-of-band attachment cannot survive a re-registration or
`tools/install-ci-runner-x64.sh --repair-labels`. ⛔ For `--repair-labels` that is a DELIBERATE,
NAMED carve-out from the union it otherwise performs, not a side effect of the union: keeping the
pin would make it permanent, so the repair drops the whole family — unless the box is opted in,
in which case it KEEPS it and prints `version-pinned, kept=[…]` — and prints
`version-pinned, not kept=[…]` otherwise (see "TWO NAMED CARVE-OUTS" below). That paragraph and
`tests/test_ci_runner_x64_install.py` must move together — a union edited back to a literal one
turns this sentence into a false claim. That is NOT the same as "cannot become
permanent": `scripts/ci_runner_gate.sh` treats any non-auto label as `extra` (`:222`) and re-emits
it in its whole-set `PUT` body on every fallback transition (`:232`), and a runner nobody
re-registers keeps whatever label it was given — so re-measure.
Nothing ASSERTS the LIVE label set: `read_pool()` reads it on every autoscale pass and
`_preserve_labels` drops a pin from it, but no test and no gate compares it to the invariant above.
Re-measure `gh api repos/<owner>/<repo>/actions/runners` if a runner's labels are re-cut.

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

- `--count N` → `tools/install-ci-runner-x64.sh:811` (the scale loop) · the two mode blocks the
  paragraphs below cite: `install` `:633` (arch refusal dies at `:638`) and `uninstall` `:686` ·
  guards at `:633-684` (forks `:641`, disk `:661`, cpu/memory `:668`) · sha256 pin + registration
  at `:822-832` (verify `:822`, token `:825`, one argv `:830-831`, `config.sh` `:832`) · systemd
  install/start at `:842-844` · label resolution at `:222-264` (`fallback_engaged` `:226`,
  `resolve_labels` `:237`) · the union's label algebra at `:266-307` and the repair that uses it,
  `repair` `:445-596` · the label-coverage block `:308-407` (`label_coverage` `:365`) and the
  `--status` block that calls it `:410-425` · `--print-labels` `:606`.
- `--count` is validated ONCE, before the mode dispatch, for EVERY mode (`:185-202`): a
  `--count 0 --uninstall` is a usage error (exit 1), not a silent no-op that reports success
  having deregistered nothing.
- **`--repair-labels` UNIONS — it can ADD a label, and the ONLY labels it removes are the two named
  carve-outs below; never `ubuntu-latest` or a custom label a workflow targets** (`:445-596`). The
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
  queue that never moves. An unreadable current label set is a REFUSAL (`:470-478`) — never "use
  the computed set", which is the same strip through a different door — but it refuses THAT RUNNER,
  not the batch: it reports exit 3 and continues, because with `--count 2` an earlier runner's PUT
  may already have succeeded and exit 2 promises "nothing was changed" (`:89-105`). The same holds
  for the loop's illegal-label and empty-union guards.
  ⛔ **OVERRIDES:** `PUT …/runners/<id>/labels` is GitHub's **Set custom labels** verb, whose own
  description is "Remove all previous custom labels and set the new custom labels" — a REPLACE of
  the runner's custom set, and exactly what `resolve_labels` alone computed here. A repair PUTs
  `current ∪ computed` instead, because a replace cannot tell "this label is not wanted" from "a
  workflow targets it": it stripped `ubuntu-latest` from the only pool answering the REQUIRED
  `python-ci-gate` aggregate (runs 40898791192 / 40901844458 — `jobs=0`, every PR blocked). The only
  removals left are the two named carve-outs below, and both are PRINTED.
  ⛔ **`relabelled …` IS A CLAIM ABOUT THE SERVER, NOT ABOUT THE PUT'S EXIT STATUS** (`:574-592`): the
  Set verb's own 200 carries the runner's resulting label set, and every label the repair asked for
  must be in it. A pool has already reported `set_labels ok=16 fail=0` for a label that was never
  attached (#6897), so a write that is accepted but not reflected is exit 3 with the returned set in
  the warning — never a `relabelled` line over a state nobody looked at.
  ⛔ **`--repair-labels --fallback off` is REFUSED (exit 2, before any read or PUT)** (`:445-452`):
  a union cannot DETACH `ubuntu-latest`, so that invocation used to be accepted, print `removed=[]`,
  say `relabelled …` and exit 0 while the label it was asked to remove stayed attached. Detaching is
  the gate's writer; by hand it is `DELETE repos/<repo>/actions/runners/<id>/labels/ubuntu-latest`.
- **⚠️ TWO NAMED CARVE-OUTS from that union, and neither is the fallback label** (`:290-306`).
  `foreign-arch` is ALWAYS dropped; the version pin only under `CI_RUNNER_OS_LABEL=off`/unset, so
  under `on|auto` it JOINS the union (printed `version-pinned, kept=[…]`, see sub-bullet 1). Both are
  PRINTED, never silently skipped — keeping either makes a false PASS permanent — and `_preserve_labels`
  drops both on re-registration, reading NO flag: ONE reader here (the repair), no writer (#10275).
  1. **`ubuntu-<version>`** (`ubuntu-24.04`, `ubuntu-22.04-arm`, …): the label a leg pins to ESCAPE
     this pool. Preserving it would un-pin `test-slow` / `workflow-lint` / `finding-provenance` /
     `mirror-images` onto a bare box whose `PATH` lacks their tools — the measured 2026-10-24
     failures (exit 127, `gh: command not found`). `ubuntu-latest` is NOT in the class (no version)
     and is always kept, as is a custom label that merely starts `ubuntu-<digits>` (the boundary
     case is `ubuntu-2204-tools`).
     ⛔ **THIS CARVE-OUT IS THE `off` STATE OF A FLAG, NOT AN UNCONDITIONAL DROP.** It drops the pin
     when `CI_RUNNER_OS_LABEL` is `off`/unset and KEEPS it under `on|auto` (printed
     `version-pinned, kept=[…]`). ⛔ WHICH STATE THE LIVE POOL IS IN CANNOT BE READ OFF THIS PAGE:
     the flag has ONE reader in this tree (this repair) and NO writer — the install-side writer ships
     with #10275, still open — so nothing in this tree exports it, unset resolves to `off`, and a plain
     `--repair-labels` performs #6897's recorded revert. Export `CI_RUNNER_OS_LABEL=on` to keep the
     pin; the removal is printed as `version-pinned, not kept=[…]` / `removed=[…]` before the PUT.
  2. **A foreign arch marker** (`self-hosted-*` other than `self-hosted-x64`): the pre-#2327 stamp
     this repair is supposed to HEAL. Keeping `self-hosted-arm64` on an x86 box makes an
     arch-specific job routable to it and produces a wrong artifact.
     ⛔ **AND THE KEEP-HALF IS ARCH-AWARE, which is what stops the union re-creating #6897's own
     defect.** A repair may run from any host against any install; if the runner's READ-ONLY host
     label says `ARM64`/`ARM` it is refused (exit 3, nothing written) instead of being PUT
     `self-hosted-x64` and KEPT on `ubuntu-latest` — that keep is exactly the measured false red (an
     arm64 box wins an `ubuntu-latest` job and dies in "Set up job", one shard per run). The
     host-derived label is the one witness a caller cannot talk the script out of.
- **`--status` answers "who answers this `runs-on:` label"** (`:308-407`): per label the workflows
  use, it counts ONLINE self-hosted runners whose label set carries it, and prints a loud `NO RUNNER
  ANSWERS '<label>'` when the count is zero. A label nobody answers does not fail — it does not run:
  the run stays `pending` with zero jobs forever, so the queue has to be READ. The label list is read
  from BOTH `.github/workflows/*.yml` and `*.yaml` (`:328`): a `.yaml` workflow dropped by the reader
  would let the block claim full coverage over a file it never read.
- **`--uninstall` deletes only what it can PROVE is a runner install, and only after its unit is
  gone** (`:686-794`; the proof is `is_runner_install` at `:218`, which accepts `.runner` or
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
- **Exit codes** (`:89-105`; `--help` prints the same block): `0` ok · `1` usage · `2` guard
  refused — headroom, arch, an undecidable fallback, an explicit `--repair-labels --fallback off`
  (the repair only unions, so it cannot detach the fallback label), or an uninstall target that is
  not provably a runner, with nothing downloaded or registered,
  so re-run with a smaller `--count` · `3`
  operation failed — download, sha256 verify, extract, token mint, `config.sh`, `svc.sh` (including
  a unit removal that failed, whose target directory is then left intact, or a deregistration that
  failed, likewise left intact), a tool missing from PATH, a failed remote read, **or a per-runner
  refusal inside the repair loop** (an unreadable label set, an illegal label, an empty union): that
  runner is skipped and the rest of the batch still runs, so the code cannot be 2.
- `--print-labels` answers BEFORE the arch and headroom guards, so the label invariant is
  checkable on any host (including the Apple-Silicon dev box and the ARM64 pool runners); the
  arch refusal still governs every INSTALL — and only an install (`:633`, the `MODE = install`
  block; the refusal itself dies at `:638`). `--status`, `--repair-labels` and `--print-labels` are
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
   (`tools/runner_autoscale.py:965` invokes the installer as `--count … --fallback auto`, never
   `--repair-labels`, and its own `_preserve_labels` already kept the label). It does NOT stop the
   remover above: the gate's `fallback_off` still strips `ubuntu-latest` from this pool on its next
   tick, from `scripts/ci_runner_gate.sh` in **agent-infra**, which is not in this checkout. So the
   label can still disappear within one tick after a hand repair — the gate-side removal is its own
   follow-up, tracked by open **#10237** — and re-measuring (`--status`, then `gh api …/runners`) is
   still the way to know.
2. **The pool is still the slower class for long legs.** These runners are not pinned off the
   fast matrix (`test (a)…(i)`), so a 20m watchdog leg can still be killed here if this box
   turns out slower than hosted under load. That is the #6897 measurement, unchanged; the
   version-pinned hosted legs stay pinned.
3. **`ubuntu-latest` remains ambiguous by design.** Any job asking for it can land on
   GitHub-hosted, an ARM64 runner, or one of these — that ambiguity is what the fallback is
   for, and why the legs that need a specific class are version-pinned.
4. **The OPT-IN split (`CI_SHARD_ROUTING=pool-split`) makes the pool a PIN on 4 of 9 required legs,
   and that exposure is deliberate but uncomfortable.** It is off by default and it is recorded in
   its own section, with an `OVERRIDES:` line (`→ The fast-matrix split is OPT-IN`). In one line:
   under `pool-split`, `test (b)`, `(d)`, `(f)` and `(h)` name `[self-hosted, self-hosted-x64]`, so a
   pool outage or a contended box holds those required checks **pending** rather than answering them
   from GitHub-hosted — the exact trade the `never to self-hosted` rule refuses, taken deliberately
   for those four rows and nowhere else.

## The box's own choice: `CI_RUNNER_OS_LABEL`

One flag decides whether a box claims a version-pinned hosted label (`ubuntu-24.04`). **It has ONE
reader in this tree — the repair's version-pin carve-out (`tools/install-ci-runner-x64.sh`, the
repair block) — and nothing WRITES it:** the install-side emitter is open PR #10275's half, so
nothing in this tree exports it, and an unexported repair therefore takes the `off` row below. WHICH
ROW IS LIVE IS A MEASUREMENT, not a fact this page holds (see *Re-measure, never assume*): a box
whose unit carries `on` KEEPS the pin. When #10275 lands both writers read the same three values.

| `CI_RUNNER_OS_LABEL` | the box claims `ubuntu-24.04` | `--repair-labels` on a runner that carries it |
| --- | --- | --- |
| `off`, or unset | no — the pre-departure invariant | DROPS it, prints `version-pinned, not kept=[…]` |
| `on` | yes | KEEPS it, prints `version-pinned, kept=[…]` |
| `auto` | yes, derived from `/etc/os-release` | KEEPS it (the box may claim one) |
| anything else | treated as `off` | DROPS it, and says the value was not understood |

- **Why the repair has to read it.** #6897's `OVERRIDES:` marker suspends the version-pin invariant
  and records the revert as `CI_RUNNER_OS_LABEL=off` **plus** `--repair-labels`. The repair honours
  that switch, and the honest consequence is that an UNSET flag IS `off`: nothing in this tree exports
  it yet (#10275), so the next manual heal on the live pool — measured 2026-10-29, 7 of 7 x64 runners
  carrying `ubuntu-24.04` — performs that revert unless the operator exports
  `CI_RUNNER_OS_LABEL=on` first. It is a PRINTED removal (`version-pinned, not kept=[…]`,
  `removed=[…]`, both before the PUT), never a silent one; and a repair that ignored the flag would
  make the recorded revert path unreachable instead.
- **What it does not do.** It does not ADD the pin: the computed set never carries an `ubuntu-<version>`
  at this head, so `on` can only PRESERVE one that is already attached (and, once #10275 lands, the
  computed set carries it and the union keeps it — the two agree either way). Restoring a pin that
  was already removed is the install path's job, never a repair's.
- **What this page still does NOT restate.** The label table above still lists `ubuntu-24.04` as
  "GitHub-hosted only", and `tools/runner_autoscale.py::_preserve_labels` still drops a version pin
  on re-registration (`docs/ops/ci-runner-x64.md:25-26` is its citation). Both belong to #10275,
  which changes the installer's DEFAULT and that preservation rule; correcting them here would put
  two lanes' edits on the same paragraphs for no behavioural gain. What THIS page changes is only
  the repair path's half of the same flag.
- **Re-measure, never assume.** The state is per-box and the fleet has moved twice in two days:
  `grep -n CI_RUNNER_OS_LABEL /etc/systemd/system/runner-autoscale.service` (the systemd path — an
  empty answer is the expected one today, since nothing in this tree writes the flag), then
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

**OVERRIDES:** the "never to `self-hosted`" rule recorded above — `pool-split` names
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
