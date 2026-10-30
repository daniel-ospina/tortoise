#!/usr/bin/env bash
# install-ci-runner-x64.sh — register x86_64 self-hosted Actions runners on THIS box,
# as members of the SAME fallback pool the five ARM64 runners serve (#5215).
#
# WHY THIS EXISTS
#   The pool was ARM64 by accident, not by requirement. Every pool-eligible job asks for
#   `ubuntu-latest` — a label, not an architecture — and the five ARM64 runners answer it
#   only because `scripts/ci_runner_gate.sh` attaches that label to them while GitHub-hosted
#   is unhealthy. The architecture itself is DERIVED BY GITHUB FROM THE HOST at registration
#   (`Linux` + `ARM64` on the Apple-Silicon VMs, `X64` here) and cannot be set by hand.
#   So a runner of either arch joins the pool by carrying the SAME custom labels; the arch
#   label stays an ornament that distinguishes the boxes without gating a single job.
#
#   Nothing here renames a job, changes a `runs-on:`, or narrows which jobs may use the
#   ARM64 pool. The workflows are untouched.
#
# ⛔ THE FALLBACK DECISION IS RESPECTED, NOT REVERSED. `scripts/ci_runner_gate.sh:1-30`
#   records that the local pool is FALLBACK-ONLY because jobs landing on local hardware
#   compete with the agent fleet (a local pytest run crashed FalkorDB, 178 restarts,
#   2026-10-12). So `ubuntu-latest` is NOT baked in at registration: `--fallback auto`
#   (the default) reads the ARM64 pool's own current state and attaches the fallback label
#   to these runners ONLY IF that pool is carrying it right now. The gate's state is the
#   source of truth; this script never decides when the pool serves CI.
#
#   ⚠️ And the reverse holds too: a runner carrying `ubuntu-latest` unconditionally makes
#   `current_mode` read `local`, so on a healthy GitHub-hosted day the gate runs `fallback_off`.
#   Since `agent-infra#2327` (PR #2338) that is harmless here: each body is derived from the
#   runner's OWN host label (`labels_for`), so it strips `ubuntu-latest` and keeps `self-hosted-x64`
#   — never stamping `self-hosted-arm64`. The label stays gate-following, but ONLY the gate can
#   DETACH it: `--repair-labels` can add it back and can never take it away, so an explicit
#   `--fallback off` on the repair path is REFUSED rather than accepted and then ignored (see the
#   repair block below, and the EXIT contract).
#
# OVERRIDES: the rule above — that an x64 runner carrying `ubuntu-latest` is a DEFECT to be
#   stripped, and `--repair-labels` implementing it by PUTting exactly the COMPUTED set (a
#   REPLACE) — assumed the ARM64 pool carries the fallback label. It does not: the five ARM64
#   runners were STRIPPED of `ubuntu-latest` on the evidence in open owner-decision issue #6897
#   (ARM boxes won `ubuntu-latest` jobs and died in "Set up job" on a codeload timeout, turning one
#   shard per run into a FALSE RED). So NOTHING answers `ubuntu-latest` today, while the REQUIRED
#   aggregate `python-ci-gate` still asks for it — and a repair that REPLACES the set strips the
#   label from the one pool left. A repair therefore UNIONS the runner's current labels with the
#   computed set, so it can never REMOVE `ubuntu-latest` or any custom label a workflow targets.
#   ⛔ Restoring the label on ARM is NOT part of this — #6897 measured that wrong.
#   ⛔ AND A UNION CANNOT DETACH, so `--repair-labels --fallback off` is REFUSED (exit 2, before any
#   read or PUT) instead of being accepted and then doing nothing to `ubuntu-latest`: on a healthy
#   hosted day the gate's state IS `off`, and the pre-refusal path printed `removed=[]`, said
#   `relabelled …`, and exited 0 while the label it was asked to detach stayed attached. Detaching
#   is the gate's writer (`fallback_off`, agent-infra) and, by hand, the per-label verb
#   `DELETE repos/$REPO/actions/runners/<id>/labels/ubuntu-latest`.
#
#   ⛔ THE UNION IS NOT UNCONDITIONAL, and the two exceptions are NOT the fallback label's. A
#   literal union would also make BOTH defects below permanent, and each is a false PASS rather
#   than a queue delay: (a) a VERSION-PINNED HOSTED label (`ubuntu-24.04`, `ubuntu-22.04-arm`, …) —
#   the label a leg pins to ESCAPE this pool, so keeping it un-pins `test-slow` / `workflow-lint` /
#   `finding-provenance` / `mirror-images` onto a bare box whose PATH lacks their tools (measured
#   2026-10-24: exit 127, `gh: command not found`); (b) a FOREIGN ARCH MARKER (`self-hosted-arm64`
#   on this x64 box) — the pre-#2327 damage a repair is expected to HEAL. (b) is ALWAYS dropped; (a)
#   only under `CI_RUNNER_OS_LABEL=off`/unset (kept — but ONLY the one spelling this box may claim,
#   `$OS_LABEL` — under `on|auto`; another image's pin such as `ubuntu-24.04-arm` stays dropped,
#   because a label names an IMAGE and keeping it is a false-architecture claim). Both are PRINTED,
#   never silently skipped. `_preserve_labels` drops both but reads NO flag.
#   `ubuntu-latest` is in neither class (it carries no version) and is always kept.
#   ⚠️ THE FLAG HAS TWO SIDES IN THIS TREE AND THEY READ THE SAME THREE VALUES. The WRITER is
#   `resolve_os_label` (`:304`), reached from `resolve_labels` — i.e. from install, repair and
#   `--print-labels` — so a runner this script registers under `on|auto` carries `ubuntu-24.04`; the
#   autoscaler's `runner_autoscale.py::_os_labels` mirrors the same rule for the ORPHANED
#   re-registration path, and `tools/install-runner-autoscale-schedule.sh` renders the value into the
#   timer so a scheduled fill inherits the box's choice. The READER that decides the carve-out is the
#   `case` in the repair block below. So an UNSET flag still takes the `off` arm and DROPS the pin
#   (a repair with no exported flag IS #6897's recorded revert: `CI_RUNNER_OS_LABEL=off` **plus**
#   `--repair-labels`), and that is now a property of the SHELL, not of the script: which row is live
#   on any box is a MEASUREMENT (`systemctl show -p Environment runner-autoscale.service`), never a
#   fact this comment can hold. Export `CI_RUNNER_OS_LABEL=on` to KEEP one.
#
# ⛔ ARCH LABELS ARE NOT INTERCHANGEABLE. A runner is registered with `self-hosted-x64`
#   (its own distinct pool marker, mirroring `self-hosted-arm64`) — NEVER with
#   `self-hosted-arm64`. Claiming another arch's label would let an x86 box serve an arch-specific
#   job and produce a wrong artifact, which is a false PASS rather than a queue delay.
#
# ⛔ `ubuntu-<version>` STAYS UNCLAIMED BY DEFAULT — and this script can now emit it on request.
#   The pin is a recorded decision (#6897/#7867): the pool is ~2x slower and killed `test-slow` at
#   its 20m watchdog, so those legs were pinned to GitHub-hosted on purpose. `CI_RUNNER_OS_LABEL`
#   therefore defaults to `off`: nothing is claimed unless an operator asks.
#   ⚠️ WHY THE ARM EXISTS AT ALL: a hosted-only label is only safe while hosted exists. Measured
#   2026-10-28, hosted was effectively unavailable (1 completed job in an hour against 25 on
#   self-hosted), so the pinned legs did not get a "queue delay" — they NEVER RAN, `python-ci-gate`
#   waited for them forever, and EVERY PR stayed BLOCKED. The gate was found queued 8 HOURS on a run
#   whose every other job had finished; it dispatched within a minute of `ubuntu-24.04` being added
#   to the pool. So the arms are the documented escape from a deadlock, not a new default:
#   `CI_RUNNER_OS_LABEL=on` (or `=auto`) claims the OS fact this box actually is (`/etc/os-release`).
#   ⛔ THE COST IS NAMED, NOT ASSUMED AWAY: on this pool a watchdog-bounded leg can be KILLED where
#   hosted finished it (#6897) — a red on healthy code. That is a red somebody sees, unlike a gate
#   that is never dispatched. Choosing between the two is an owner decision, recorded in
#   `docs/ops/ci-runner-x64.md`; this flag is how either choice is applied without another code change.
#
# USAGE
#   tools/install-ci-runner-x64.sh --count 2                  # install/repair runners 1..2
#   tools/install-ci-runner-x64.sh --count 2 --dry-run        # plan + headroom arithmetic only
#   tools/install-ci-runner-x64.sh --status                   # remote view + label coverage + units
#   tools/install-ci-runner-x64.sh --repair-labels --count 2  # UNION the computed labels in (drops a version pin unless CI_RUNNER_OS_LABEL=on + a foreign arch marker; refuses --fallback off — it cannot DETACH)
#   tools/install-ci-runner-x64.sh --uninstall --count 2      # deregister + remove
#   tools/install-ci-runner-x64.sh --print-labels             # the label set config.sh would get
#   tools/install-ci-runner-x64.sh --help
#   --fallback auto|on|off   (default auto = follow the ARM64 pool's current state)
#
# REBOOT: the runner is a systemd service (`svc.sh install`), enabled at install time, so it
#   comes back on boot with no human step. See docs/ops/ci-runner-x64.md.
#
# EXIT — the one distinction a caller can key on: "retry with a smaller --count" must never be
#   the same exit code as "the install broke".
#     0  ok
#     1  usage — bad flag, bad --count, bad sizing override. Nothing was attempted.
#     2  GUARD refused — headroom / arch / undecidable fallback / `--repair-labels --fallback off`
#        (a repair only UNIONS, so it cannot DETACH the fallback label) / an `--uninstall` target
#        that is not provably a runner install. Nothing was downloaded, registered or removed;
#        re-run with a smaller --count, on an x86_64 host, with --fallback auto|on, or with the
#        right CI_RUNNER_BASE.
#     3  OPERATION failed — download, sha256 verify, extract, token mint, config.sh, svc.sh (a
#        failed systemd unit removal, or a failed `config.sh remove`, included — that target is then
#        LEFT intact rather than deleted out from under an enabled unit or a live registration), a
#        tool missing from PATH, a failed remote read (`--status`), or a PER-RUNNER refusal inside
#        the repair loop (an unreadable label set, an illegal label, an empty union): that runner is
#        skipped and the rest of the batch still runs, because an earlier runner may ALREADY have
#        been relabelled and exit 2 promises "nothing was changed".
#        (`--repair-labels` against a `gh` that returns no id is 3 for the same reason.)
#     4  label-coverage could not read the workflows, so it has NO verdict — run `--status` from
#        the directory holding `.github/workflows`. An empty read is not a clean bill of health, and
#        neither is a PARTIAL one: a `runs-on:` whose labels are not a static label set (a `${{ … }}`
#        expression in the value, in a member, or INSIDE one — `ubuntu-24.04-${{ matrix.arch }}` — or
#        a spelling this parser cannot carry as one label: a space-bearing token, a YAML alias, a
#        block-scalar indicator) is refused, never cleared.
#     5  label-coverage WAS read and found a required label this pool is ASKED to serve with NO
#        self-hosted answer — the erosion state: the operator opted in, and the label is not there.
#        A zero-answer label this pool is NOT asked to serve (`CI_RUNNER_OS_LABEL=off`, the shipped
#        default, or `auto` declining) is a REPORT, not a verdict: #6897's pin applied correctly OR
#        erosion to zero — one shell cannot tell them apart. 0 does not mean "covered".
set -uo pipefail

# The help text is the file's OWN header, cut at the closing `set` line rather than a frozen line
# range: a range stops matching the moment a USAGE row is inserted above it, and it then silently
# truncates `--uninstall`, `--fallback`, the REBOOT note and the EXIT contract (which is what
# `sed -n '1,44p'` did).
usage() { awk '/^# USAGE/{p=1} /^set -uo pipefail$/{p=0} p' "$0" | sed 's/^# \{0,1\}//'; }

REPO="${CI_RUNNER_REPO:-daniel-ospina/tortoise}"
# ⛔ `--status`'s label-coverage block reads `.github/workflows` from the CALLER'S CWD, not from the
# checkout this script lives in — the label contract it checks is the one in the tree you are
# standing in. That is why it refuses (exit 4) rather than reporting coverage when there is nothing
# to read there: run it from the repo root you mean to check.
BASE="${CI_RUNNER_BASE:-/opt/gh-runner-x64}"
NAME_PREFIX="${CI_RUNNER_NAME_PREFIX:-pi-runner-x64}"
# Where `svc.sh install` writes the unit, and the ONE path this script stats to decide whether a
# unit exists. Overridable for the same reason `CI_RUNNER_ARCH`/`CI_RUNNER_NCPU` are: the box that
# reads this code is not always the box being guarded, and a test cannot write /etc. It changes no
# behaviour on a real host.
UNIT_DIR="${CI_RUNNER_UNIT_DIR:-/etc/systemd/system}"
RUNNER_VERSION="${CI_RUNNER_VERSION:-2.337.0}"   # match the ARM64 pool's version
# THIS arch's own pool marker (never `self-hosted-arm64`).
ARCH_LABEL="self-hosted-x64"
# The label the ARM64 pool's own marker is (used only to READ the gate's state).
ARM_POOL_LABEL="self-hosted-arm64"
# The fallback label the gate attaches/detaches — never permanent (see header).
FALLBACK_LABEL="ubuntu-latest"
# ⛔ THE OS-RELEASE LABEL IS AN OS FACT, NOT A FALLBACK — and omitting it blocks EVERY PR.
# The required legs `workflow-lint` and `test-slow (a–e)` in .github/workflows/python-ci.yml name
# `runs-on: ubuntu-24.04`. The only other pool that answers that label is GitHub-HOSTED, and hosted
# is effectively unavailable here (measured 2026-10-28: 1 completed hosted job/hour against 92 jobs
# on self-hosted in 30 min). A runner that omits it therefore makes those legs UNSCHEDULABLE, and
# `python-ci-gate` — the REQUIRED aggregate whose `needs:` includes them — is never dispatched, so
# every PR stays BLOCKED no matter how much capacity the fleet has.
# Measured the same night: the gate had been queued 8h on a run whose every other job had completed,
# and it dispatched within a minute of this label being added (on pi-runner-cmux-x64-6).
# Derived from /etc/os-release below in the `auto` arm only (`on` claims it unconditionally, which is
# why `on` is the wrong value on a box that is not 24.04); an unrecognised value is a usage error
# in EVERY mode, including `--status` and `--uninstall` (validated once, before the dispatch).
OS_LABEL="ubuntu-24.04"
# The OS NAME and OS-RELEASE reads are seams for the same reason `CI_RUNNER_ARCH` / `CI_RUNNER_NCPU`
# are: the box that reads this code is not always the box being guarded, so the `auto` arms must be
# exercisable on a non-Linux dev box (and on a Linux one that is not 24.04) rather than only on the
# one host whose answer happens to be `on`. They change no behaviour on a real host.
OS_NAME_SEAM="${CI_RUNNER_OS_NAME:-}"
OS_RELEASE_SEAM="${CI_RUNNER_OS_RELEASE:-/etc/os-release}"
# Measured on a real job (2026-10-22, this box, `legal-e2e`): one python process saturated a
# core (99.3%) with apt at 46% alongside — i.e. ~1.0-1.5 cores per concurrent job, plus the
# per-job docker/FalkorDB it starts. 2 cores/job is the sizing unit, and 2 cores are held back
# for the agent lanes that are this box's reason to exist.
# Sizing unit per runner and the reserves held back for the agent lanes. Env-overridable so a
# caller can exercise ONE bound at a time: the guards run forks → disk → cpu/memory, so a test
# that pins the cpu arithmetic had to hope this box's disk and fork cap let it through (review
# finding 3 — on a hosted ubuntu-latest ~14G free on /, the disk bound refused FIRST and the
# assertion on the cpu message failed). Every value is validated as a non-negative integer below.
CORES_PER_RUNNER="${CI_RUNNER_CORES_PER_RUNNER:-2}"
CORES_RESERVED_FOR_AGENT_LANES="${CI_RUNNER_CORES_RESERVED:-2}"
DISK_PER_RUNNER_GB="${CI_RUNNER_DISK_PER_RUNNER_GB:-8}"   # checkout (~0.2G) + uv venv + docker images, measured per lane
DISK_RESERVE_GB="${CI_RUNNER_DISK_RESERVE_GB:-20}"        # keeps the agent lanes' worktrees safe
MEM_PER_RUNNER_GB="${CI_RUNNER_MEM_PER_RUNNER_GB:-2}"
MEM_RESERVE_GB="${CI_RUNNER_MEM_RESERVE_GB:-4}"
FORKS_PER_RUNNER="${CI_RUNNER_FORKS_PER_RUNNER:-400}"     # a pytest job + its docker children ≈ 400 procs
# The lane jobs need these on PATH; the runner registration needs the others. #9018 is the
# class where a pool runner lacks one and the job exits 2 (a false red, not a queue delay).
JOB_TOOLS="gh unzip docker"
RUNNER_TOOLS="gh curl tar sha256sum"

# sha256 of actions-runner-linux-x64-$RUNNER_VERSION.tar.gz, read from the release asset's
# published digest (`gh api repos/actions/runner/releases/tags/v$RUNNER_VERSION`). Pinned:
# this tarball is fetched over the network and immediately executed.
SHA256_LINUX_X64="70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613"

COUNT=""; DRY=0; MODE=install; FALLBACK=auto
while [ $# -gt 0 ]; do
  case "$1" in
    --count) COUNT="${2:-}"; shift 2 2>/dev/null || shift ;;
    --count=*) COUNT="${1#*=}"; shift ;;
    --dry-run) DRY=1; shift ;;
    --uninstall) MODE=uninstall; shift ;;
    --status) MODE=status; shift ;;
    --repair-labels) MODE=repair; shift ;;
    --print-labels) MODE=labels; shift ;;
    --fallback) FALLBACK="${2:-}"; shift 2 2>/dev/null || shift ;;
    --fallback=*) FALLBACK="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "usage: $0 [--count N] [--dry-run] [--uninstall] [--status] [--repair-labels] [--print-labels] [--fallback auto|on|off]" >&2; exit 1 ;;
  esac
done
# ⛔ NOT `${COUNT:-2}` AT ASSIGNMENT TIME: a literal `COUNT=0` is "set", so a `:-` default would
# never fire and `seq 1 0` would make `--status` / `--uninstall` a silent no-op that reports
# success. An empty COUNT is the only "unset" here — and the default is
# applied ONCE, by the validation block below, for EVERY mode.
DEFAULT_COUNT=2

say()  { printf '%s\n' "$*" >&2; }
die()  { say "REFUSED: $1"; exit "${2:-2}"; }
need() { command -v "$1" >/dev/null 2>&1 || die "'$1' is not on PATH (a runner without it cannot register, and a job without it exits 2 — #9018)" 3; }

# ⛔ VALIDATED ONCE, BEFORE THE MODE DISPATCH — not inside `install`. Validated on the install
# path only, `--status` / `--repair-labels` / `--uninstall` still run `seq 1 "$COUNT"`: a
# `--count 0` runs an EMPTY loop that exits 0 (deregisters nothing, reports success), and
# `--count abc` lets `seq` fail to stderr into the same empty loop with the same exit 0 — the
# caller cannot tell "removed" from "typed the count wrong".
case "$COUNT" in
  '') COUNT="$DEFAULT_COUNT" ;;
  *[!0-9]*) die "--count must be a positive integer (got '$COUNT')" 1 ;;
  *) [ "$COUNT" -ge 1 ] || die "--count must be >= 1 (got $COUNT)" 1 ;;
esac
# The sizing overrides get the same treatment: a bound nobody can read must be REFUSED, never
# arithmetically coerced into a number ($(( )) turns 'x' into 0, i.e. silently into no bound).
for _size in CORES_PER_RUNNER CORES_RESERVED_FOR_AGENT_LANES DISK_PER_RUNNER_GB \
             DISK_RESERVE_GB MEM_PER_RUNNER_GB MEM_RESERVE_GB FORKS_PER_RUNNER; do
  case "${!_size}" in
    ''|*[!0-9]*) die "$_size must be a non-negative integer (got '${!_size}')" 1 ;;
  esac
done

runner_dir() { # $1 = instance number: instance 1 keeps the base dir, the rest get -<n>
  if [ "$1" = 1 ]; then printf '%s' "$BASE"; else printf '%s-%s' "$BASE" "$1"; fi
}
unit_for() { printf 'actions.runner.%s.%s.service' "$(printf '%s' "$REPO" | tr '/' '-')" "$1"; }
# Is this directory PROVABLY a runner install? Only such a directory may be deleted by
# `--uninstall`; see the uninstall block for why "it is a directory" is not enough.
#
# ⛔ `config.sh` ALONE IS **NOT** PROOF, and it used to be accepted as such. It is a very common
# filename, and a directory holding nothing but an executable `config.sh` that exits 0 passes
# `./config.sh remove …` — so the uninstall loop reached `sudo rm -rf` on a tree this installer
# never created (measured: `deregistered proj`, EXIT=0, tree gone). It also adds no coverage: the
# install path extracts the whole tarball with ONE `tar xzf`, so `config.sh` and `bin/Runner.Listener`
# always land together — a lone `config.sh` is a state this script cannot produce. The proof is
# therefore `config.sh` WRITTEN BY A REGISTRATION (`.runner`) or the listener binary itself.
is_runner_install() {
  [ -f "$1/.runner" ] || [ -f "$1/bin/Runner.Listener" ]
}

# ── the fallback label: follow the gate's own state, never assume it ───────────
# Reads whether the ARM64 pool is CURRENTLY carrying the fallback label — the exact condition
# `ci_runner_gate.sh` establishes. Unreadable is 2 (fail closed), never "off": guessing here is
# what would either starve CI or hand this box a permanent claim on the pool.
fallback_engaged() {
  local pages n
  pages="$(gh api --paginate "repos/$REPO/actions/runners" --jq \
    "[.runners[] | select([.labels[].name] | index(\"$ARM_POOL_LABEL\")) | select([.labels[].name] | index(\"$FALLBACK_LABEL\"))] | length" 2>&1)" || return 2
  case "$pages" in ''|*[!0-9[:space:]]*) return 2 ;; esac
  case "$pages" in *[0-9]*) : ;; *) return 2 ;; esac
  n="$(printf '%s\n' "$pages" | awk '{s+=$1} END{printf "%d", s+0}')" || return 2
  case "$n" in ''|*[!0-9]*) return 2 ;; esac
  [ "$n" -gt 0 ] && echo on || echo off
}

# ── the OS-label mode: validated ONCE, for EVERY mode ─────────────────────────────────────────
# ⛔ AN UNKNOWN VALUE IS REFUSED, never a silent no-op: `CI_RUNNER_OS_LABEL=true|ON|1` used to print
# a label list with no `ubuntu-24.04` and exit 0, so an operator who mistyped believed the deadlock
# escape was applied while every PR stayed BLOCKED — the exact silent failure this flag exists to
# remove. Nothing has been installed at this point, so the exit is 1 (usage).
#
# ⛔ AND IT IS VALIDATED HERE, NOT ONLY IN `resolve_labels`. The check used to live inside
# `resolve_os_label`, which is reached only from `resolve_labels` — i.e. from install / repair /
# --print-labels. `--status` never calls it, so `CI_RUNNER_OS_LABEL=true --status` exited 0 with no
# complaint, and `--status` is the mode an operator runs to VERIFY the escape is applied: a typo was
# silent in exactly the one place it is looked for. `--uninstall` never called it either. Same
# pattern as `--count` and the sizing units above: read once, before the mode dispatch.
case "${CI_RUNNER_OS_LABEL:-off}" in
  on|off|auto) : ;;
  *) die "CI_RUNNER_OS_LABEL must be off|on|auto (got '$CI_RUNNER_OS_LABEL')" 1 ;;
esac

resolve_os_label() { # sets OS_LABELS ("" or ",ubuntu-24.04"); OFF by default, never guessed
  local os_name released
  OS_LABELS=""
  case "${CI_RUNNER_OS_LABEL:-off}" in
    on)  OS_LABELS=",$OS_LABEL"; return 0 ;;
    off) return 0 ;;
    auto)
      # ⛔ `auto` DECLINING MUST SAY SO. It used to return 0 in silence, so a 22.04 box, a container
      # with no /etc/os-release, or a macOS lane got `--print-labels` without `ubuntu-24.04` and no
      # signal at all — indistinguishable from "opted out", on the one property (the host's OS
      # version) that decides whether the deadlock escape is applied. `on`'s refusal is loud; the
      # RECOMMENDED spelling must not be the quiet one.
      os_name="${OS_NAME_SEAM:-$(uname -s 2>/dev/null)}"
      if [ "$os_name" != Linux ]; then
        say "os-label: auto DECLINED — host is ${os_name:-unknown}, not Linux; $OS_LABEL not claimed"
        return 0
      fi
      # The subshell keeps os-release's NAME/VERSION_ID out of this script's environment.
      # shellcheck disable=SC1091
      released="$( ( . "$OS_RELEASE_SEAM" 2>/dev/null; printf '%s' "${VERSION_ID:-}" ) )"
      if [ "$released" = "${OS_LABEL#ubuntu-}" ]; then
        OS_LABELS=",$OS_LABEL"
      else
        # ⛔ NAME THE MEASUREMENT, not the expectation: "does not report VERSION_ID=24.04" is a
        # claim about what we wanted, so an operator on a 22.04 box could not tell whether the read
        # failed or the box really is 22.04.
        say "os-label: auto DECLINED — $OS_RELEASE_SEAM reports VERSION_ID=${released:-<none>}, not ${OS_LABEL#ubuntu-}; $OS_LABEL not claimed"
      fi
      return 0 ;;
  esac
}

resolve_labels() { # sets LABELS; exit 2 when the state cannot be read
  local engaged
  resolve_os_label
  case "$FALLBACK" in
    on)  LABELS="$ARCH_LABEL,$FALLBACK_LABEL" ;;
    off) LABELS="$ARCH_LABEL" ;;
    auto)
      # A dry run must work on a box with no gh/auth: the read is a planning dependency, not a
      # decision, so it is skipped and the plan says the fallback label is resolved at install —
      # TRUE FOR INSTALL ONLY. `--repair-labels` never installs and its plan IS the body it would
      # PUT (`dry-run: would PUT $body …`), so a short-circuit there printed a request the real run
      # does not send (measured: `{self-hosted-x64}` planned, `{self-hosted-x64,ubuntu-latest}` sent
      # on an engaged pool). Repair mode calls `need gh` before this, so the read is available.
      if [ "$DRY" = 1 ] && [ "$MODE" != repair ]; then
        LABELS="$ARCH_LABEL$OS_LABELS"
        say "fallback: auto — decided at install time from the ARM64 pool's state (not read in a dry run)"
        return 0
      fi
      engaged="$(fallback_engaged)" || die "cannot read whether the ARM64 pool is carrying $FALLBACK_LABEL — refusing to guess which pool this runner joins. Re-run with --fallback on or --fallback off." 2
      # ⛔ THE LOG MUST PRINT THE SET THAT IS REGISTERED. `LABELS` is completed by `$OS_LABELS` at the
      # end of this function, so printing `$LABELS` here logged `labels=self-hosted-x64,ubuntu-latest`
      # four lines before the runner was registered with `…,ubuntu-24.04` — inside ONE run, and the
      # runbook quotes this line as recorded output, so an operator checking the log for the
      # mitigation saw it absent and concluded the flag had not applied (the shape this whole change
      # removes). Appending `$OS_LABELS` in the message keeps ONE append point for `LABELS`.
      if [ "$engaged" = on ]; then
        LABELS="$ARCH_LABEL,$FALLBACK_LABEL"
        say "fallback: ENGAGED on the ARM64 pool now → labels=$LABELS$OS_LABELS"
      else
        LABELS="$ARCH_LABEL"
        say "fallback: not engaged on the ARM64 pool now → labels=$LABELS$OS_LABELS (the gate attaches $FALLBACK_LABEL when it falls back)"
      fi ;;
    *) die "--fallback must be auto|on|off (got '$FALLBACK')" 1 ;;
  esac
  # The OS fact rides along on every arm: it is not a fallback state, so `--fallback off` must not
  # drop it — dropping it is what leaves the required ubuntu-24.04 legs with no self-hosted answer.
  LABELS="$LABELS$OS_LABELS"
}

# ── label algebra: the UNION a repair PUTs ─────────────────────────────────────
# Host-derived labels GitHub OWNS. They come back in every runner's label list, they cannot be set
# by hand (HTTP 422 `Cannot set labels that duplicate existing read-only labels`, measured
# 2026-10-27), and they are not part of the custom set this script manages — so they are filtered
# out of the PUT body. Dropping them is not a loss: GitHub re-derives them from the host.
# ⛔ EXACT SPELLINGS ONLY, deliberately. Matching these case-insensitively (with `tr`) would also
# swallow a CUSTOM label an operator created — e.g. a `linux` or `x64` of their own — and filtering
# a label out of the PUT body IS a removal, which is the one thing this block must never do.
# GitHub returns the reserved spellings below and refuses to have them re-set; nothing else is
# touched.
is_host_label() {
  case "$1" in
    self-hosted|Linux|macOS|Windows|X64|ARM64|ARM) return 0 ;;
  esac
  return 1
}
# Is $2 already in the comma-separated list $1? (`case`, not an array: bash 3.2 has no assoc arrays.)
list_has() { case ",$1," in *",$2,"*) return 0 ;; esac; return 1; }
# Is $1 an ARCH/POOL marker that is NOT this box's (`self-hosted-*` other than `$ARCH_LABEL`)?
# It must be dropped by a repair: `self-hosted-arm64` is a CUSTOM label (GitHub returns only
# `self-hosted`, `Linux`/`macOS`/`Windows` and `X64`/`ARM64`/`ARM` as read-only), so a union that
# keeps it leaves an x86 box advertising an arm64 pool — the false PASS this file forbids at
# `:75-78`, and the pre-#2327 stamp a repair exists to heal. `--repair-labels` therefore always
# puts back THIS arch's marker and only this one, which is what `_preserve_labels` re-derives too. ⛔ MATCHED CASE-INSENSITIVELY (`tr`), UNLIKE `is_host_label` ABOVE — the exact-case rule does NOT transfer to this class: docs.github.com "apply labels" states "Labels are case-insensitive", and a CUSTOM label's spelling is whatever created it, so `Self-Hosted-Arm64` / `SELF-HOSTED-ARM64` ARE the arm64 marker and still answer a lower-case `runs-on:` — an exact-case test here is how a mixed-case stamp escapes both heal paths and keeps the false PASS alive.
is_foreign_arch_label() {
  case "$(printf '%s' "$1" | tr 'A-Z' 'a-z')" in
    "$ARCH_LABEL") return 1 ;;
    self-hosted-*) return 0 ;;
  esac
  return 1
}
# Is $1 a VERSION-PINNED HOSTED label (`ubuntu-24.04`, `ubuntu-22.04-arm`, …)? Same class/shape as
# `tools/runner_autoscale.py::VERSION_PINNED_HOSTED_LABEL` — one invariant, two writers. The pool
# claims `ubuntu-latest` and NEVER an `ubuntu-<version>` (`docs/ops/ci-runner-x64.md:63`) — that is
# the label a leg pins to escape this pool; `ubuntu-2204-tools` is NOT in the class (boundary test in
# `tests/test_runner_autoscale.py`), hence both ends anchored. ⛔ A HERE-STRING, never a builtin
# writer piped into a quiet grep (#841): under `pipefail` the quiet grep's early exit leaves the
# writer with SIGPIPE and the PIPELINE non-zero over text that DID match — a false negative here would drop this carve-out and put a version pin back on the pool. `repo-harnesses` enforces the shape.
is_version_pinned_hosted_label() {
  grep -qiE '^ubuntu-[0-9]+(\.[0-9]+)*(-arm)?$' <<<"$1"
}

# Print the OFFLINE half of a coverage measurement: context, never coverage. GitHub's own runner
# status list defines `Offline` as "the runner is not connected to GitHub", so an offline runner
# cannot be handed a job — the ONLINE count is the answer, and this line is what stops a pool of
# carriers that have all fallen over from reading as covered. Called on every verdict arm.
offline_context() { # $1=label  $2=offline carriers of it  $3=offline runners in the pool
  if [ "$3" -gt 0 ]; then
    say "     Offline, so NOT counted as an answer: $3 runner(s) in the pool are not \`online\`, $2 of them carrying $1."
  fi
}

# ── status ─────────────────────────────────────────────────────────────────────
if [ "$MODE" = status ]; then
  need gh
  # `--paginate`: the same one-page truncation the coverage read below had, on the read that
  # answers "is this runner registered and idle".
  gh api --paginate "repos/$REPO/actions/runners" \
    --jq '.runners[] | "\(.name)\t\(.status)\tbusy=\(.busy)\t\([.labels[].name]|join(","))"' \
    || exit 3
  for i in $(seq 1 "$COUNT"); do
    d="$(runner_dir "$i")"
    [ -f "$d/.runner" ] || continue
    agent="$(sed -n 's/.*"agentName": "\([^"]*\)".*/\1/p' "$d/.runner")"
    unit="$(unit_for "$agent")"
    state="$(systemctl is-enabled "$unit" 2>/dev/null || echo missing)/$(systemctl is-active "$unit" 2>/dev/null || echo inactive)"
    say "dir=$d unit=$unit enabled/active=$state"
  done
  # ── label coverage: the contract this script exists to keep ──────────────────────────────────
  # A `runs-on:` label with NO self-hosted answer can only be served by GitHub-HOSTED, and hosted is
  # effectively unavailable here (measured 2026-10-28: 1 completed hosted job/hour against 92 jobs on
  # self-hosted in 30 min). That is the shape that left `python-ci-gate` queued for 8 HOURS on a run
  # whose every other job had finished: `workflow-lint` and `test-slow (a–e)` ask for `ubuntu-24.04`,
  # no runner answered it, so the required aggregate was never dispatched and every PR stayed BLOCKED.
  # Run this from the repo root — it reads the workflows to learn which labels must be answerable.
  say "label-coverage — labels the workflows name vs runners answering them:"
  # ⛔ ONE PAGINATED READ OF (name, status, labels), NOT A ONE-PAGE LABEL TALLY. `per_page=100` with
  # no `--paginate` silently truncated a pool larger than 100 and reported a covered label as `0
  # runners` (a false red on a large pool) — the read directly above it (`fallback_engaged`) is
  # paginated for the same reason. The runner COUNT comes out of the same read, which is what lets
  # the partial-coverage arm below exist at all: a label tally cannot tell 12 runners from 1.
  # ⛔ AND THE ROW CARRIES `.status`, BECAUSE A LABEL ON AN OFFLINE RUNNER IS NOT AN ANSWER. GitHub's
  # own runner list defines `Offline` as "the runner is not connected to GitHub" (and `Idle` as
  # connected and ready) — an offline runner cannot be handed a job, so counting it as coverage
  # reports the exact incident this block exists to surface as GREEN: 7 carriers of `ubuntu-24.04`,
  # all offline, printed `7 of 7` with no `⚠️ PARTIAL` and exit 0 under `CI_RUNNER_OS_LABEL=on`.
  # Runners go offline routinely (that is what `runner_autoscale.py::converge`'s DEAD/ORPHANED
  # re-registration exists for), so this is a state, not an edge case. The count below is therefore
  # ONLINE-only, and the offline rows are counted as CONTEXT and printed.
  pool_tsv="$(gh api --paginate "repos/$REPO/actions/runners" --jq '.runners[] | [.name, .status, ([.labels[].name]|join(","))] | @tsv' 2>/dev/null)" || exit 3
  # ⛔ THE RUNNER COUNT IS A MEASUREMENT, AND IT IS THE DENOMINATOR — so it is PASSED to the awk
  # below, not re-inferred there. `awk` counts RECORDS, and an empty pool still feeds it ONE empty
  # record (`printf '%s\n' ""`), so `total` came out as 1 and an empty pool printed
  # `0 of 1 self-hosted runner(s)` — a fabricated runner, in the one block whose doctrine is "an
  # empty read is not a coverage verdict". The correct 0 was computed here and then never read.
  pool_total="$(printf '%s\n' "$pool_tsv" | grep -c . || true)"
  case "$pool_total" in ''|*[!0-9]*) pool_total=0 ;; esac
  # …and the ONLINE half of it, from the same read: the denominator of every class below is a
  # population that can ANSWER, so it is the online population. An empty online pool keeps the
  # pre-existing `0 of 0` reading (a measurement), never a silent fallback to the offline rows.
  pool_online="$(printf '%s\n' "$pool_tsv" | awk -F'\t' '$2 == "online" {n++} END {printf "%d", n+0}')"
  case "$pool_online" in ''|*[!0-9]*) pool_online=0 ;; esac
  # ⛔ THE BARE-PIN VERDICT READS THE OPERATOR'S INTENT, NOT ONLY THE COUNT — the same predicate
  # this block already applies to `ubuntu-latest` ("a pool carrying no `ubuntu-latest` IS the healthy
  # state, because the gate is not routing it here"). Without it the block verdicts the CONFIGURATION
  # #6897 REQUIRES as the incident state: the shipped default is `off`, so a pool that correctly
  # answers no `ubuntu-24.04` — the pin, and the exact state this file's own revert recipe
  # (`CI_RUNNER_OS_LABEL=off` plus `--repair-labels`) produces — exited 5, and the remedy it printed
  # (`CI_RUNNER_OS_LABEL=on …`) re-applied a departure the owner has not settled. `--status` is the
  # mode the runbook tells an operator to VERIFY with, so a permanently-red instrument there is the
  # false red this block's neighbouring comment refuses to create for `ubuntu-latest`.
  # The intent is READ FROM THE SAME FUNCTION THE INSTALL PATH USES (`resolve_os_label`, whose
  # validation arm is 300 lines above), so the verdict and the label set a re-registration would
  # actually get cannot disagree: `on` claims it, `auto` claims it only on a matching host, `off`
  # (and `auto` declining) does not.
  #
  # ⛔ BUT IT IS THE INTENT OF **THIS SHELL**, WHICH IS NOT A PROPERTY OF THE POOL. A shell variable
  # cannot say what the last installer pass, the autoscaler timer, or a peer lane asked for — so it
  # may only ever ADD a verdict (the pool is asked ⇒ a zero answer is the incident), never SUBTRACT
  # one. The runbook tells the operator to verify with the BARE `--status` (`docs/ops/ci-runner-x64.md`
  # §*Verify it*), and in that invocation this variable is unset — i.e. `off` — so reading it as a
  # pool fact made the DEFAULT invocation clear exactly the erosion this block exists to see, and
  # made the `⚠️ PARTIAL` arm assert "this pool is not asked to serve it" on a line that had just
  # MEASURED carriers answering it. A runner's labels are explicitly assigned (GitHub REST:
  # `Add custom labels` / `Set custom labels` / `Remove all custom labels`; registration labels come
  # from `config.sh --labels`), so `n > 0` is direct evidence that SOME actor asked the pool to serve
  # the label — and a carrier outvotes a shell.
  #
  # So the two inputs stay SEPARATE and both are printed:
  #   * `shell_asks_os_label` — what this shell's `CI_RUNNER_OS_LABEL` resolves to;
  #   * `n` — the measurement. `n > 0` short-circuits the intent read at every consumer below.
  # When the shell does not ask and `n == 0`, the honest output is the MEASUREMENT plus the
  # ambiguity (the pin applied correctly, or erosion to zero — one shell cannot tell them apart);
  # never an assertion of the harmless reading, which is a fail-open in the blocking direction.
  resolve_os_label
  case "$OS_LABELS" in
    *",$OS_LABEL") shell_asks_os_label=1 ;;
    *)              shell_asks_os_label=0 ;;
  esac
  uncovered=0
  # ⛔ A WHOLE-LINE YAML COMMENT IS NOT A `runs-on:` LINE. Unanchored, `# legacy: runs-on: windows-2022`
  # was counted as a required label and printed a false ⛔ UNCOVERED, and this tree's own comments
  # name both real labels — the block printed 85/12 where the real lines are 77/9 (2026-10-29).
  # Both arms are needed: strip whole-line comments, AND anchor to the line's first token — anchoring
  # is what stops a TRAILING comment (`runs-on: x  # note: runs-on: y`) contributing `y`.
  # ⛔ AND `*.yaml` IS AS REAL AS `*.yml` — GitHub accepts both. Globbing only `*.yml` read NOTHING
  # on a repo whose workflows are `.yaml`, which the empty-read arm below then reports as exit 4 (a
  # false failure, not a false pass) while `--status` never sees the labels it exists to check.
  # ⛔ EXTRACTION IS A STATE MACHINE, NOT THREE GREPS. The pipeline this replaces required the
  # value on the SAME line and unquoted DOUBLE quotes only, so three of the four DOCUMENTED
  # `runs-on` forms were cleared to exit 0 — measured against the pre-fix script:
  # `runs-on: ['ubuntu-24.04']` kept its single quotes, matched no class arm, fell to the `*)`
  # report arm and left `uncovered=0`; a BLOCK sequence (`runs-on:` then `- ubuntu-24.04`) and a
  # `group:`/`labels:` mapping yielded NOTHING AT ALL, so the label was never counted and the read
  # looked covered. GitHub documents all four forms (a single string, an ARRAY of strings, a
  # variable, and a `key: value` pair using `group`/`labels`), so those are not exotic spellings —
  # they are most of the documented surface, and the incident state this block exists to surface is
  # exactly what they express. Every one of them reported success, in the one direction the header
  # above forbids.
  # The arms: a same-line scalar or flow sequence; a block sequence; a mapping whose `labels:` key
  # is a flow list, a scalar or a nested block list (`group:` names a runner GROUP, not a label, so
  # it is consumed and never counted); and single quotes unquoted alongside double quotes.
  # ⛔ ONE UNREADABLE MEMBER MAKES THE WHOLE BLOCK UNREADABLE — a `!` line, never a silent drop.
  # The extractor used to print `!` only when the block yielded NOTHING (`got == 0 && !pgroup`), so
  # any parseable sibling ABSORBED an unparsed one and the block cleared: `labels: ${{ … }}` beside
  # `group: pool` emitted no label and no marker (the `group:` suppressed it), and
  # `runs-on: [self-hosted, "${{ matrix.arch }}"]` emitted `self-hosted` and dropped the expression
  # member. GitHub documents BOTH shapes — the combined `group:`/`labels:` form is its own example
  # (`docs.github.com/…/self-hosted-runners/use-in-a-workflow`), and a matrix-ified workflow's first
  # move is to put the OS into the label list — so a REQUIRED leg can be routed that way, and the
  # instrument would have reported it `covered` and exited 0. That is the incident state this block
  # exists to surface, cleared by the one arm that was meant to fail closed.
  # So: an expression member (or a `labels:` key that yields no static label at all, even with a
  # `group:` named beside it) prints `!`, exactly like a block with no readable label and no group.
  coverage="$(grep -rhvE '^[[:space:]]*#' .github/workflows/*.yml .github/workflows/*.yaml 2>/dev/null \
      | awk '
      # ⛔ CRLF: `\r` is not `[ \t]`, so a CRLF workflow left `ubuntu-24.04\r` in the emitted
      # label — a token that matches no class arm, so it fell to the "a report, not a block" default
      # and the whole read silently became "not a label this pool serves", exit 0 (measured: the
      # real carriers of that label were never read). GitHub accepts CRLF YAML.
      function trim(s) { sub(/\r+$/, "", s); gsub(/^[ \t]+/, "", s); gsub(/[ \t]+$/, "", s); return s }
      # ⛔ A KEY IS A NODE, NOT A SPELLING — for `group:`/`labels:` exactly as for `runs-on:`.
      # YAML gives a plain, a single-quoted and a double-quoted key the SAME mapping key (1.2.2
      # §7.4.2 / §8.2.2), and GitHub routes all three the same way. Matched on the SPELLING only, a
      # quoted `labels:` was not a `labels:` key at all: it emitted no label, `plabels` stayed 0, and
      # a `group:` in the same mapping suppressed the one refusal marker (the `finish()` condition
      # `!pgroup || plabels`) — so a required `ubuntu-24.04` was never measured and `--status`
      # exited 0. Measured: `runs-on: {group: pool, "labels": [ubuntu-24.04]}` → rc 0 with no
      # `ubuntu-24.04` row; the same mapping with a plain `labels:` → rc 5.
      function keyis(k, name) { return k ~ ("^[\"\047]?" name "[\"\047]?[ \t]*:") }
      function unq(s) {
        s = trim(s)
        while (s ~ /^["\047]/) s = substr(s, 2)
        while (s ~ /["\047]$/) s = substr(s, 1, length(s) - 1)
        return trim(s)
      }
      function emit(t,   v) {
        v = unq(t)
        if (v == "") return
        # ⛔ `bad++`, NOT a silent `return`: a value that is not a static label is not one this pass
        # can read, so the BLOCK has no static label set and must reach the exit-4 arm. Dropping it
        # let a parseable sibling (`self-hosted`) answer for the whole block.
        # ⛔ AND THE TEST IS CONTENT, NOT PREFIX. `ubuntu-24.04-${{ matrix.arch }}` is the ordinary
        # way a required leg gets parameterised, and a prefix test (`/^\$/`) printed it as a literal
        # label name — so the block had a readable label set, no `!` was emitted, and the one
        # instrument meant to surface the all-PR-BLOCKED state exited 0 over it. `${{` ANYWHERE is
        # the predicate a comparable applies (actionlint `ast.go` `ContainsExpression` is
        # `strings.Index(s, "${{")`, not a prefix test) and what the EXIT contract in this file
        # promises. Whitespace and a YAML alias (`*pool`) are refused for the same reason (a token
        # word-splits through the unquoted `for label in $coverage_labels` into FABRICATED labels)
        # — and so is a BLOCK-SCALAR INDICATOR (`>`, `>-`, `|`, `|+`): that is the scalar HEADER,
        # not a label, so `runs-on: >-` printed `⛔ >- = 0 of 1` and exited 0 with its value unread.
        if (v ~ /\$\{\{/ || v ~ /[ \t]/ || v ~ /^[*&]/ || v ~ /^[>|]/) { bad++; return }
        print v; got++
      }
      function flowlist(s,   n, i, a) {
        s = trim(s); sub(/^\[/, "", s); sub(/\]$/, "", s)
        n = split(s, a, ",")
        for (i = 1; i <= n; i++) emit(a[i])
      }
      function mapping(s,   m, i, c, depth, cur, parts, n, k) {
        m = s; sub(/^\{/, "", m); sub(/\}[ \t]*$/, "", m)
        n = 0; cur = ""; depth = 0
        for (i = 1; i <= length(m); i++) {
          c = substr(m, i, 1)
          if (c == "[" || c == "{") depth++
          else if (c == "]" || c == "}") depth--
          if (c == "," && depth == 0) { parts[++n] = cur; cur = ""; continue }
          cur = cur c
        }
        if (trim(cur) != "") parts[++n] = cur
        for (i = 1; i <= n; i++) {
          k = trim(parts[i])
          if (keyis(k, "group")) pgroup = 1
          else if (keyis(k, "labels")) { plabels = 1; value(substr(k, index(k, ":") + 1)) }
        }
      }
      # ⛔ AN UNCLOSED COLLECTION IS REFUSED, NOT TRUNCATED — the same content-not-prefix rule
      # `emit()` applies to a VALUE, applied to the FORM. `flowlist()` strips a `]` that may not be
      # there (`sub(/\]$/, "", s)` is a silent no-op), so a multi-line flow sequence — valid YAML,
      # and what a formatted workflow produces — was read as its opening line and `finish()`ed on
      # the spot: `runs-on: [self-hosted,` / `ubuntu-24.04]` emitted `self-hosted`, dropped the pin
      # on the continuation line, and exited 0. Measured: that fixture → rc 0 with NO
      # `ubuntu-24.04` row; the identical array on one line → rc 5. Joining continuation lines would
      # also close it; refusing is the fail-closed half of the contract stated above (exit 4, "no
      # verdict"), and a form this pass cannot read is refused rather than cleared.
      function value(s) {
        s = trim(s)
        if (s ~ /^\[/) {
          if (s !~ /\]$/) { bad++; return }
          flowlist(s); return
        }
        if (s ~ /^\{/) {
          if (s !~ /\}$/) { bad++; return }
          mapping(s); return
        }
        emit(s)
      }
      # ⛔ THE KEY IS A NODE, NOT A SPELLING — AND A KEY THIS PASS OPENS NO BLOCK FOR IS REFUSED.
      # The opener below read ONE spelling (`^runs-on[ \t]*:`), so a key written as a quoted scalar
      # (single- or double-quoted `runs-on`) or in explicit-key form (`? runs-on` then `: x`) opened
      # no block: `finish()` never ran, so NO `!` was emitted, `runs_on_unreadable` stayed 0, and the
      # two "no verdict" arms could not fire (this repo always names a readable label) — the block
      # printed a clean row set and exited 0 having never measured that label. That is the false
      # PASS this block exists to prevent, and it is the one spelling a machine-formatted workflow
      # produces. GitHub routes all of them the same way: YAML gives a plain, a single-quoted, a
      # double-quoted and an explicit key the SAME mapping key (1.2.2 §7.4.2 / §8.2.2 — measured
      # with PyYAML, all four resolve to `runs-on`, value included). So the two scalar spellings are
      # READ, and every key-shaped line is COUNTED against the blocks opened, so the form this pass
      # still cannot carry is refused rather than clearing them all — the same content-not-prefix
      # rule `emit()` applies to a VALUE, applied to the KEY.
      function keylike(s) {
        return (s ~ /^["\047]?runs-on["\047]?[ \t]*:/ \
             || s ~ /^\?[ \t]+["\047]?runs-on["\047]?[ \t]*$/)
      }
      function finish() {
        if (!pend) return
        # ⛔ `bad > 0` FIRST: an unreadable MEMBER refuses the block even when a sibling parsed, and
        # a `labels:` key that produced no static label refuses it even when a `group:` was named
        # (`!pgroup` alone let that shape through — the group suppressed the marker entirely).
        # A `group:` with genuinely NO `labels:` key stays a report: there is no label there to
        # check, which is a real read, not a failed one.
        if (bad > 0 || (got == 0 && (!pgroup || plabels))) print "!"
        pend = 0
      }
      {
        raw = $0
        line = raw
        sub(/^[ \t]+/, "", line)
        if (line == "") next
        ind = index(raw, line) - 1
        sub(/[ \t]+#.*$/, "", line)   # a TRAILING comment must not contribute a second label
        line = trim(line)
        if (line == "") next
        if (pend && ind <= pind) finish()
        if (keylike(line)) keyseen++
        if (line ~ /^["\047]?runs-on["\047]?[ \t]*:/) {
          opened++
          finish()
          rest = trim(substr(line, index(line, ":") + 1))
          pend = 1; pind = ind; got = 0; pgroup = 0; bad = 0; plabels = 0
          if (rest != "") { value(rest); finish() }
          next
        }
        if (pend && ind > pind) {
          if (line ~ /^-[ \t]/) {
            item = trim(substr(line, 2))
            if (keyis(item, "group")) pgroup = 1
            else if (keyis(item, "labels")) { plabels = 1; value(substr(item, index(item, ":") + 1)) }
            else value(item)
          }
          else if (keyis(line, "group")) pgroup = 1
          else if (keyis(line, "labels")) {
            plabels = 1
            v = substr(line, index(line, ":") + 1)
            if (trim(v) != "") value(v)
          }
          else finish()
          next
        }
      }
      END {
        finish()
        # One `!` per key-shaped line that opened no block: the count in the refusal message is the
        # number of `runs-on` keys this pass could not read, not a boolean.
        for (i = opened; i < keyseen; i++) print "!"
      }')"
  # ⛔ A `runs-on:` THE EXTRACTION COULD NOT READ IS `exit 4`, NEVER A SILENT 0. The two exit-4 arms
  # below catch a read that produced NOTHING; they cannot catch one that is PARTIAL, which is what
  # an unrecognised spelling produces whenever any other workflow names a label. A matrix expression
  # (`runs-on: ${{ matrix.os }}`) is the same case for a different reason: its label set is not
  # static, so there is nothing to verdict. Refusing converts the next spelling nobody thought of
  # from a false green into an honest no-verdict — the choice every other arm here already made.
  runs_on_unreadable="$(printf '%s\n' "$coverage" | grep -c '^!' || true)"
  case "$runs_on_unreadable" in ''|*[!0-9]*) runs_on_unreadable=0 ;; esac
  # ⛔ THE LABEL SET IS COLLECTED ONCE, BEFORE THE LOOP, BECAUSE THE REFUSALS BELOW MUST RUN BEFORE
  # ANY VERDICT. Both the `labels_seen == 0` read and the loop iterate the same set, so they share
  # this one pipeline — two readers, one read.
  coverage_labels="$(printf '%s\n' "$coverage" | grep -v '^!' | sort -u)"
  labels_seen=0
  if [ -n "$coverage_labels" ]; then
    labels_seen="$(printf '%s\n' "$coverage_labels" | grep -c . || true)"
  fi
  case "$labels_seen" in ''|*[!0-9]*) labels_seen=0 ;; esac
  # ⛔ ALL THREE "NO VERDICT" ARMS RUN **BEFORE** THE LOOP THAT PRINTS VERDICTS. They used to sit
  # after it, so a repo with ONE unreadable `runs-on:` block AND a zero-answer `ubuntu-24.04` under
  # `CI_RUNNER_OS_LABEL=on` printed the full ⛔ UNCOVERED verdict and its `--repair-labels` remedy
  # and THEN exited `4` — the exit code disowning the verdict it had just printed (the EXIT header
  # and the runbook define `4` as "NO verdict"), and the printed remedy fixing nothing, since the
  # cause is an unparsed block and relabelling runners cannot parse it. That is the same "a red
  # whose printed remedy cannot work is a false red" rule this block applies to `ubuntu-22.04` and
  # to the gate-owned `ubuntu-latest`. A partial read refuses before it verdicts anything.
  # ⛔ "NOTHING TO CHECK" IS NOT "ALL COVERED". Run from a directory with no .github/workflows (or on
  # a repo whose workflows name no single-label runs-on) the loop below prints its header, zero rows,
  # and exits 0 — a clean bill of health from the one instrument meant to prove coverage. Refuse
  # instead, with its own exit code so a caller can tell it apart from a real zero-answer label.
  # ⛔ EACH GLOB IS TESTED SEPARATELY: `ls *.yml *.yaml` exits 2 when only ONE of them matches, so a
  # repo with valid `.yaml` workflows and no `.yml` reads as "nothing there" and exits 4.
  if [ ! -d .github/workflows ] \
      || { ! ls .github/workflows/*.yml >/dev/null 2>&1 && ! ls .github/workflows/*.yaml >/dev/null 2>&1; }; then
    say "⛔ label-coverage read NOTHING: no .github/workflows/*.yml|*.yaml in $(pwd) — run --status from the repo root. An empty read is not a coverage verdict."
    exit 4
  fi
  # ⛔ AND A PARTIAL READ IS NOT A VERDICT EITHER. This arm is what makes the extractor's blind
  # spots fail CLOSED: a `runs-on:` it produced no label for (an expression, or a spelling it does
  # not parse) is no-verdict, not covered. It sits before the covered/uncovered verdict so an
  # unreadable construct can never be outvoted by the labels that did parse.
  if [ "$runs_on_unreadable" -gt 0 ]; then
    say "⛔ label-coverage could not read $runs_on_unreadable runs-on: block(s) as a STATIC label set (a matrix/expression value, or a YAML spelling this parser does not understand) — a partial read is not a coverage verdict."
    exit 4
  fi
  # ⛔ AND THE DIRECTORY EXISTING IS NOT THE SAME PREDICATE: a repo whose workflows are all `uses:`
  # re-usable-workflow callers (or any shape with no single-label `runs-on:`) satisfies the arm above
  # and still yields ZERO rows. Counting the labels the loop actually FOUND is the only read that
  # distinguishes "covered" from "did not look" — measured: a dir with one `uses:`-only workflow
  # printed the header, no rows, and exited 0.
  if [ "$labels_seen" -eq 0 ]; then
    say "⛔ label-coverage parsed .github/workflows/*.yml|*.yaml in $(pwd) and found NO runs-on label to check — an empty read is not a coverage verdict."
    exit 4
  fi
  for label in $coverage_labels; do
    # ⛔ n OF WHO? The denominator has to be the runners that COULD answer this label, or the
    # reading is a false red on a two-arch pool: the ARM64 runners must never claim the bare
    # `ubuntu-<version>` (that is the arch invariant `_preserve_labels` now enforces), so
    # `7 of 12` on the live pool would read as erosion when it is exactly correct. A bare version
    # label is measured against the x64 pool, an `-arm` spelling against the arm64 pool, and
    # anything else against the whole pool.
    # ⛔ THE CLASS IS DECIDED ONCE, HERE, AND THE VERDICT BELOW READS IT. Two definitions of "a
    # bare version pin" used to sit eight lines apart — the denominator matched
    # `^ubuntu-[0-9][0-9.]*$` while the verdict used the `case` glob `ubuntu-[0-9]*`, which is
    # looser by whatever suffix follows: `ubuntu-2204-tools` is NOT the class (its denominator is
    # the whole pool) yet the verdict arm claimed it, so a zero-carrier CUSTOM label exited `5` and
    # was told "python-ci-gate is never dispatched and every PR stays BLOCKED" — a false red from
    # the one block that must not produce one, and the mirror of the boundary
    # `tools/runner_autoscale.py:284-287` states ("a custom label that merely starts
    # `ubuntu-<digits>` … is NOT in the class"). One predicate, two readers.
    # ⛔ THE NUMERATOR IS COUNTED IN THE SAME POPULATION AS THE DENOMINATOR, OR THE RATIO CAN
    # EXCEED 1 AND THE EROSION ARM SWITCHES ITSELF OFF. The denominator was fixed to the sub-pool
    # above; the numerator was not, so it counted EVERY carrier in the pool — including the ARM64
    # runners whose answer that denominator explicitly does not count. Measured on a 12-runner
    # pool shaped like the drift this PR records (3 of 7 x64 carriers + 5 arm64 runners wrongly
    # carrying the bare pin) the pre-fix program printed `8 of 7 runner(s)` with NO `⚠️ PARTIAL`,
    # because `n < denom` is false as soon as `denom - n_actual` foreign carriers are present —
    # `8 > 7`, so the one read that sees erosion before the last carrier goes was unreachable, and
    # the autoscaler could walk the x64 sub-pool to zero with this instrument green at every step.
    # It is not hypothetical and it is not "still coverage": per the published label→arch mapping a
    # bare `ubuntu-<version>` IS the x64 image, so an arm64 runner claiming it is a false
    # architecture claim — the exact state `_preserve_labels` now drops on re-registration, and
    # never evidence that the label is served. So `hit` is carried together with the class
    # membership the loop already computes (`nx` = bare-class carriers, `na` = arm-class carriers)
    # and the END block selects the numerator by the SAME class it selected the denominator by, so
    # `num <= denom` holds by construction. Mutation: use `n` as the numerator again -> `8 of 7`,
    # no PARTIAL -> tests/test_ci_runner_x64_install.py
    # ::test_label_coverage_reports_partial_coverage_rather_than_green RED.
    counts="$(printf '%s\n' "$pool_tsv" | awk -F'\t' -v want="$label" -v x64label="$ARCH_LABEL" -v armlabel="$ARM_POOL_LABEL" \
      -v fallbacklabel="$FALLBACK_LABEL" -v pool_total="$pool_total" -v pool_online="$pool_online" '
      {k=split($3,a,","); hit=0; x64=0; arm=0
       for(i=1;i<=k;i++){ if(a[i]==want) hit=1; if(a[i]==x64label) x64=1; if(a[i]==armlabel) arm=1 }
       # ⛔ ONLINE IS A GATE, NOT A DECORATION: an offline runner is "not connected to GitHub", so
       # it cannot be handed the job. Every count below is online-only, which is what makes an
       # all-offline pool read `0 of N` (a verdict) instead of `N of N` (a false green).
       if ($2 == "online") { online++
         if (hit) n++; if (x64) nx64++; if (arm) narm++
         if (hit && x64) nx++; if (hit && arm) na++ }
       else if (hit) offhit++ }        # carriers that CANNOT answer — context, never coverage
      END { total = pool_total + 0   # the MEASURED runner count, never the record count above
            denom = pool_online       # every class below is measured against online runners
            cls = "other"
            num = n                 # whole-pool classes count the whole pool, numerator included
            if (want ~ /^ubuntu-[0-9][0-9.]*-arm$/) { denom = narm; cls = "arm"; num = na }
            else if (want == fallbacklabel) cls = "fallback"
            else if (want ~ /^ubuntu-[0-9][0-9.]*$/) { denom = nx64; cls = "bare"; num = nx }
            # ⛔ AN EMPTY SUB-POOL IS THE INCIDENT — NEVER WIDEN THE NUMERATOR TO THE WHOLE POOL. `n`
            # counts every online carrier, so widening (which this line did until review) counted
            # foreign-arch runners wrongly carrying the label as coverage — 0 x64 + 5 arm64
            # carriers read `5 of 5` / rc 0. The numerator stays class-paired: `0 of 0` is honest and
            # the zero arm above verdicts it. No-op for `fallback`/`other`, where both were already
            # the whole pool. Pinned by test_label_coverage_never_widens_a_class_numerator_to_foreign_arch_carriers.
            printf "%d %d %s %d %d", num+0, denom+0, cls, offhit+0, total-online }')"
    n="${counts%% *}"
    rest="${counts#* }"
    denom="${rest%% *}"
    rest="${rest#* }"
    label_class="${rest%% *}"
    rest="${rest#* }"
    off_hit="${rest%% *}"
    off_pool="${rest#* }"
    case "$off_hit" in ''|*[!0-9]*) off_hit=0 ;; esac
    case "$off_pool" in ''|*[!0-9]*) off_pool=0 ;; esac
    # ⛔ THE INTENT IS PER LABEL, NOT PER BLOCK. `resolve_os_label` can only ever set `$OS_LABEL`
    # (`ubuntu-24.04`), but the `bare` class the awk defines is ANY `^ubuntu-[0-9][0-9.]*$` — so
    # with the flag `on`, a workflow pinning `ubuntu-22.04` at zero took the verdict arm, exited `5`,
    # and printed a remedy (`CI_RUNNER_OS_LABEL=on --repair-labels`) that CANNOT apply it: the
    # installer only ever emits `$OS_LABEL`. A permanent red with an unactionable remedy is the
    # false-red class this block refuses to create, and the drift `tools/runner_autoscale.py:284-287`
    # names ("a custom label that merely starts `ubuntu-<digits>` … is NOT in the class") in the
    # other direction. So `$OS_LABEL` is the ONLY label this script can be asked to serve; every
    # other bare spelling is somebody else's registration and is reported, never verdicted.
    lab_asked=0
    if [ "$label" = "$OS_LABEL" ] && [ "$shell_asks_os_label" = 1 ]; then lab_asked=1; fi
    if [ "$n" -eq 0 ]; then
      # ⛔ WHOSE PROBLEM IS A ZERO? Only a label this pool is ASKED to serve — and the three classes
      # answer differently, so the verdict is per-class rather than "any zero is the incident":
      #
      #  * a BARE `ubuntu-<version>` IS the incident class: pinned to GitHub-hosted on purpose,
      #    hosted effectively unavailable here, so zero self-hosted answers = every PR blocked.
      #  * `ubuntu-latest` at zero is the HEALTHY state, and it is self-proving: the gate attaches
      #    that label to the ARM64 pool when it falls back, so a pool where NO runner carries it is
      #    a pool the gate is not routing to. Verdicting it would make `--status` — and any
      #    `--status && …` guard — permanently red in normal operation: the false red this
      #    instrument must not produce. (This is why there is no "is the fallback engaged" read
      #    here: it cannot be engaged while the label has no carrier.)
      #  * an `-arm` spelling, `macos-*`, `windows-*` and custom labels are not this pool's to
      #    serve; their hosted availability was never measured, so report and do not verdict.
      #
      # ⛔ REPORT THE MEASUREMENT, NOT A CAUSE: the line used to assert "only GitHub-hosted can
      # answer it, and hosted is not available here" for EVERY zero-answer label.
      say "  ⛔ $label = 0 of $denom online self-hosted runner(s) answer it."
      # Offline carriers are printed as context on EVERY arm, because they are the one reading that
      # distinguishes "nobody was ever asked" from "asked, and every carrier has fallen over" —
      # the state the coverage count must never absorb as coverage.
      offline_context "$label" "$off_hit" "$off_pool"
      # The class came from the awk above, so this arm and the DENOMINATOR cannot disagree.
      case "$label_class" in
        arm)
          say "     An ARM64 hosted spelling — not this pool's class on an x64 box (a false architecture claim). A report, not a block."
          ;;
        fallback)
          say "     GitHub-hosted serves $FALLBACK_LABEL while the gate is not routing it here — a report, not a block."
          ;;
        bare)
          # ⛔ WHAT WAS MEASURED, AND WHAT FOLLOWS ONLY CONDITIONALLY. The measurement is "no
          # self-hosted runner answers this label"; "every PR stays BLOCKED" is a consequence that
          # holds only WHILE hosted is also unavailable (the 2026-10-28 outage). On a healthy day the
          # pinned legs dispatch on hosted by design — that is what the pin is for — so the verdict
          # states the measurement and makes the consequence conditional instead of asserting it.
          if [ "$label" != "$OS_LABEL" ]; then
            say "     A bare hosted-version pin, but not the one this script registers (it only ever emits $OS_LABEL): nothing here re-applies $label, so its zero answer is not this pool's verdict — a report, not a block."
          elif [ "$lab_asked" = 1 ]; then
            say "     Only GitHub-hosted can answer it, and this pool IS asked to serve it (CI_RUNNER_OS_LABEL=${CI_RUNNER_OS_LABEL:-off}) — so a required leg naming it has NO self-hosted answer: the mitigation is not applied. On a hosted outage that is every PR BLOCKED; on a healthy day it is the #6897 pin in effect."
            uncovered=1
          else
            # ⛔ THE SHELL'S FLAG IS NOT THE POOL'S STATE, SO A ZERO CANNOT BE NAMED. `n == 0` is
            # consistent with BOTH "never asked" (the #6897 pin applied correctly, nothing to do)
            # and "asked and gone" (erosion to zero — the incident, and the exact event this block
            # was given a PARTIAL arm to see coming). This arm used to assert the harmless reading,
            # which is a fail-open in the blocking direction: it cleared the erosion the doc says the
            # block surfaces, in the DEFAULT invocation the runbook tells the operator to verify with
            # (bare `--status`, variable unset ⇒ `off`). Report the measurement plus the ambiguity.
            say "     Two readings fit that measurement, and CI_RUNNER_OS_LABEL=${CI_RUNNER_OS_LABEL:-off} is THIS SHELL's intent — not the pool's: the #6897 pin applied correctly (nothing to do), or a pool that WAS serving it and has eroded to zero (the incident state, on a hosted outage). A zero answer cannot distinguish \"never asked\" from \"asked and gone\"."
            say "     Runbook §*Verify it* has the read that settles it from the pool's own record; to see the verdict as if it IS asked, re-run with CI_RUNNER_OS_LABEL=on — \`--status\` is read-only, so that changes nothing on the pool. A report, not a block."
          fi
          ;;
        *)
          say "     Not a label this pool serves (hosted-only or custom), so its zero answer blocks nothing here — a report, not a block."
          ;;
      esac
    elif [ "$n" -lt "$denom" ]; then
      # ⛔ COVERED IS NOT THE SAME AS FULLY COVERED, and the difference is the whole erosion path:
      # the scheduled autoscaler ADDS runners, and a fill/repair pass that omits the label leaves
      # the pool drifting back to zero answers one runner at a time with this instrument green at
      # every step. `0 < n < denom` is the only read that sees it before the last one goes.
      say "  $label = $n of $denom online runner(s) — ⚠️ PARTIAL: the other $((denom - n)) cannot answer it."
      offline_context "$label" "$off_hit" "$off_pool"
      # ⛔ THE REMEDY IS CLASS-BLIND HERE TOO, AND THIS IS WHERE IT BITES. The zero-answer arm above
      # decides per class; this one printed the bare-pin instruction for EVERY class. So a pool
      # mid-fallback-transition printed `ubuntu-latest = 5 of 12 … re-apply with
      # CI_RUNNER_OS_LABEL=on --repair-labels` for a label the GATE owns and toggles, and a
      # deliberately partial custom label got the same instruction. Following it cannot work: only
      # the bare `ubuntu-<version>` pin is re-applied by this script at all. The class is already
      # computed (same awk as the denominator), so use it.
      case "$label_class" in
        bare)
          # ⛔ THE REMEDY CARRIES THE PRECONDITION IT NEEDS, and never prescribes a side of the open
          # decision: telling an operator whose shell says `off` to "re-apply with
          # CI_RUNNER_OS_LABEL=on" would reverse #6897 in passing.
          if [ "$label" != "$OS_LABEL" ]; then
            say "     A bare hosted-version pin this script does not register (it only ever emits $OS_LABEL), so nothing here re-applies $label — a report."
          elif [ "$lab_asked" = 1 ]; then
            say "     A re-registration or fill that omits it erodes coverage silently; re-apply with CI_RUNNER_OS_LABEL=${CI_RUNNER_OS_LABEL:-off} --repair-labels."
          else
            # ⛔ CARRIERS ARE INTENT — THIS ARM USED TO INVERT THE REMEDY ON THE OPPOSITE READING.
            # Reaching here MEANS `n > 0`: the pool has just been MEASURED carrying the label. A
            # runner's labels are explicitly assigned (GitHub REST: `Add custom labels` / `Set
            # custom labels` / `Remove all custom labels`; registration labels come from `config.sh
            # --labels`) — a runner never acquires one on its own — so the carriers are proof the
            # pool WAS asked, and "this pool is not asked to serve it" was asserted on a line
            # reporting three runners answering it. Worse, the remedy it printed (`--repair-labels`
            # with the variable UNSET) PUTs a WHOLE label set (`set`, not `add`), so following it
            # removes the label from the LAST carriers and lands the pool at `0 of $denom` — which
            # the zero arm above would then report as intentional. That is a destructive instruction
            # printed as the fix for an identical measurement, keyed on a shell variable that carries
            # no pool state. State both readings and refuse to prescribe: the measurement is
            # `n of denom carry it, flag not set in THIS shell`, and both "leftover" and "eroding"
            # fit it — only one of them is safe to act on, so the operator decides from the pool's
            # own record, not from here.
            say "     $n runner(s) DO carry it, and CI_RUNNER_OS_LABEL=${CI_RUNNER_OS_LABEL:-off} is only THIS SHELL's intent: a runner's labels are assigned explicitly, so a carrier is proof the pool WAS asked at some point. Two readings fit — a leftover from an earlier \`on\` period (the revert removes it), or erosion while the pool is still asked (re-apply)."
            say "     ⛔ Do NOT settle it by accident: \`--repair-labels\` PUTs a WHOLE label set, so running it with the variable UNSET removes $OS_LABEL from the last $n carrier(s) and lands the pool at 0 of $denom. A report, not a block."
          fi
          ;;
        arm)
          say "     An ARM64 hosted spelling: only the arm64 pool may claim it, and nothing on this x64 box re-applies it — a report."
          ;;
        fallback)
          say "     The gate attaches and detaches $FALLBACK_LABEL itself, so a partial count is the gate mid-transition — nothing to re-apply here."
          ;;
        *)
          say "     A custom label this pool does not fully carry. This script does not register it; nothing to re-apply here."
          ;;
      esac
    else
      say "  $label = $n of $denom online runner(s)"
      offline_context "$label" "$off_hit" "$off_pool"
    fi
  done
  if [ "$uncovered" = 0 ]; then
    :
  else
    # ⛔ THE REMEDY IS PRINTED AS A COMMAND WITH ITS PRECONDITION, not as a parenthetical. `--repair-labels`
    # PUTs a WHOLE label set (`set`, not `add`), so running the bare command in a fresh shell — where
    # `CI_RUNNER_OS_LABEL` is unset and therefore `off` — REMOVES $OS_LABEL from every runner and
    # re-creates the deadlock, while the operator's intent was to fix coverage. The one instruction
    # this instrument emits in its failure case must not be the one that undoes the mitigation.
    # ⛔ AND IT REPEATS THE OPERATOR'S OWN SPELLING, never a hardcoded `on`: reaching this arm means
    # the pool IS asked to serve the label, and `auto` (which may have resolved to claimed on THIS
    # host) has to survive the re-run. Prescribing `on` would silently un-narrow an `auto` install.
    say "⛔ UNCOVERED label(s) above. This pool IS asked to serve them and does not. To (re-)apply this script's label set INCLUDING $OS_LABEL, run:"
    say "     CI_RUNNER_OS_LABEL=${CI_RUNNER_OS_LABEL:-off} $0 --repair-labels --count <N>"
    say "   Run it with the variable UNSET and the PUT body has no $OS_LABEL in it — that REMOVES the mitigation from every runner it touches."
  fi
  # ⛔ THE INCIDENT STATE IS NOT SUCCESS. Exit 0 used to cover BOTH "every label covered" and "a
  # required label has no runner at all" — so `--status && …` could not tell the state that blocks
  # every PR from the one that does not. 4 already means "no verdict"; 5 is "a verdict, and it is
  # bad". No caller in the tree invokes this script's `--status`: the repo-wide `--status` hits
  # are other tools' own flags plus its tests and the runbook's examples, so none changes meaning.
  if [ "$uncovered" = 1 ]; then exit 5; fi
  exit 0
fi

# ── repair: re-apply the gate-following label set to already-registered runners ──
# ⛔ A REPAIR UNIONS; IT CAN NEVER REMOVE A LABEL A WORKFLOW NEEDS. This block used to PUT exactly
# the set `resolve_labels` computed, so every repair silently REPLACED the runner's labels. With
# the ARM64 pool stripped of `ubuntu-latest` (#6897) `--fallback auto` now computes
# `self-hosted-x64` alone — so a repair removed `ubuntu-latest` from the x64 pool, which is the
# label the REQUIRED `python-ci-gate` aggregate is pinned to. The run then sat `pending` with ZERO
# materialised jobs (no red, no green, every PR blocked), and a label restored by hand did not
# survive the next repair (measured 2026-10-27: gone within ~25 minutes, twice).
#
# So: read the runner's CURRENT labels, union the computed set INTO them, and PUT THAT. Two classes
# are NOT carried into the union — a VERSION-PINNED HOSTED label (`ubuntu-<version>`, which a leg
# pins to escape this pool) and a FOREIGN ARCH MARKER (`self-hosted-*` other than this box's) —
# because keeping either makes a false PASS permanent; both are PRINTED on the `— not kept=` line.
# The host-derived labels GitHub owns (`self-hosted`, `Linux`/`macOS`/`Windows`, `X64`/`ARM64`/`ARM`)
# are not sent either: the API refuses them (HTTP 422 `Cannot set labels that duplicate existing
# read-only labels`), and they are PRINTED, never silently dropped. The before/after sets and the
# derived `removed=` list are printed BEFORE the PUT, so a removal is visible in the output rather
# than inferred from a queue that never moves.
if [ "$MODE" = repair ]; then
  # ⛔ AN EXPLICIT `--fallback off` IS REFUSED, NOT ACCEPTED-AND-IGNORED. The union never subtracts,
  # so this invocation cannot do the one thing it asks for: it used to print `removed=[]`, say
  # `relabelled … → self-hosted-x64,ubuntu-latest` and exit 0 while the label stayed attached. The
  # refusal is BEFORE any read and any PUT (exit 2 is the honest code: nothing was changed), and it
  # names the path that CAN detach. `auto` already covers the operator's real intent — on a healthy
  # hosted day it computes `self-hosted-x64` alone and the union simply keeps what is there.
  if [ "$FALLBACK" = off ]; then
    die "--repair-labels only UNIONS: it cannot DETACH $FALLBACK_LABEL, so --fallback off would be accepted and then do nothing to it. Use --fallback auto (a repair follows the gate by ADDING), or detach the label on purpose: gh api -X DELETE repos/$REPO/actions/runners/<id>/labels/$FALLBACK_LABEL" 2
  fi
  need gh
  resolve_labels
  rc=0
  for i in $(seq 1 "$COUNT"); do
    d="$(runner_dir "$i")"
    [ -f "$d/.runner" ] || { say "skip: $d is not configured"; continue; }
    agent="$(sed -n 's/.*"agentName": "\([^"]*\)".*/\1/p' "$d/.runner")"
    # ONE read, so the id and the label set cannot disagree: `id<TAB>label,label,…`.
    detail="$(gh api --paginate "repos/$REPO/actions/runners" --jq ".runners[]|select(.name==\"$agent\")|\"\(.id)\t\([.labels[].name]|join(\",\"))\"" | head -1)"
    id="$(printf '%s' "$detail" | cut -f1)"
    current="$(printf '%s' "$detail" | cut -f2)"
    # An operation that was ATTEMPTED and failed is 3, never 1: the EXIT contract above reserves 1
    # for "usage — nothing was attempted", and a caller keying on it would read a failed relabel as a
    # bad flag (measured: `--repair-labels` against a `gh` that returns no id exited 1).
    case "$id" in ''|*[!0-9]*) say "WARNING: $agent not found in the repo's runners"; rc=3; continue ;; esac
    # ⛔ AN UNREADABLE LABEL SET IS A REFUSAL, NEVER "the computed set, then". Falling back to the
    # computed set IS the replace this block exists to stop: on a failed read it would drop
    # `ubuntu-latest` by the same silent route, with only the transport error to show for it.
    # ⛔ AND IT REFUSES **THIS RUNNER**, NOT THE BATCH: a `die` here exits the whole script, so with
    # `--count 2` runner 1's PUT could already have succeeded when runner 2 hit this guard and the
    # caller would receive exit 2 = "Nothing was downloaded, registered or removed" for a run that
    # relabelled a runner. The pre-fix loop met the analogous per-runner condition with
    # `say WARNING; rc=3; continue`, and the three guards inside this loop keep that contract.
    if [ -z "$current" ]; then
      say "WARNING: cannot read the current labels of $agent (id $id) — refusing to PUT a set that may REPLACE the labels this runner already carries (a replace is how $FALLBACK_LABEL was lost). Nothing was changed for this runner; re-run once the read succeeds."; rc=3; continue
    fi
    # ⛔ GLOBBING OFF WHILE A LABEL SET IS SPLIT. Every loop below walks a comma-separated list with
    # an UNQUOTED expansion, so a label carrying `*`, `?` or `[` is expanded against the operator's
    # cwd before the loop ever sees it — and the vendor's PUT schema declares no pattern, so the
    # charset that stops one arriving is server-side only. Measured: `current=…,*` became
    # `after=[self-hosted-x64,DECOY-1.txt,DECOY-2.log]` with `removed=[]`, and the PUT then attached
    # the operator's own filenames to the runner as labels — a write the `removed=` line cannot show.
    # `set -f` makes the split literal, so such a label reaches the illegal-label guard below and
    # REFUSES the runner. It is restored after the last split (before the `relabelled` line); a
    # `continue` on a refusal path leaves it set, and every such path either re-sets it at the top of
    # the next iteration or leaves it to `exit` at the end of this block — nothing past the loop runs.
    set -f
    after=""; host_skipped=""; arch_dropped=""; pinned_dropped=""; pinned_kept=""; host_arch=""
    for l in $(printf '%s' "$current" | tr ',' ' '); do
      if is_host_label "$l"; then
        # the ONE honest witness of the box's architecture: GitHub derives these from the HOST.
        case "$l" in ARM|ARM64) host_arch="$l" ;; X64) host_arch="X64" ;; esac
        host_skipped="${host_skipped:+$host_skipped,}$l"; continue
      fi
      # ⛔ THE TWO NAMED CARVE-OUTS (see the header). Dropping them is a REMOVAL, so each is
      # PRINTED — a union that silently kept a false arch claim or a version pin is the defect this
      # loop's own `removed=` line exists to expose.
      if is_foreign_arch_label "$l"; then arch_dropped="${arch_dropped:+$arch_dropped,}$l"; continue; fi
      # ⛔ THE VERSION-PIN CARVE-OUT IS THE FLAG'S `off` STATE, NOT AN UNCONDITIONAL DROP. #6897's
      # `OVERRIDES:` marker suspends the version-pin invariant for as long as hosted deadlocks, it
      # measures the departure as APPLIED (2026-10-29: 7 of 7 x64 runners carry `ubuntu-24.04`), and
      # it records the revert as `CI_RUNNER_OS_LABEL=off` **plus** `--repair-labels`. A repair that
      # dropped the pin whatever the flag said reached that revert with NO SWITCH INVOLVED: the next
      # manual heal would have reverted an applied owner departure while the operator read a warning
      # about a label their own box had been asked to claim. So the box's choice is READ here, under
      # the same name and values the install-side contract uses (#10275): `on|auto` keeps the pin
      # (the mitigation IS this box's state), `off`/unset drops it (the pre-departure invariant, and
      # what `_preserve_labels` drops on re-registration). An unrecognised value never reaches here —
      # the flag is validated ONCE, before the mode dispatch (`die … must be off|on|auto`), so the
      # `*)` arm below is unreachable by construction and kept only so a future edit that moves the
      # validation cannot silently turn a typo into a KEEP.
      if is_version_pinned_hosted_label "$l"; then
        case "${CI_RUNNER_OS_LABEL:-off}" in
          on|auto)
            # ⛔ THE KEEP IS NARROWED TO THE ONE LABEL THIS BOX MAY CLAIM. `$OS_LABEL` is the only
            # spelling `resolve_os_label` can ever emit, and a label is a claim about the IMAGE, not
            # about architecture: keeping `ubuntu-24.04-arm` here would let an x86_64 runner serve
            # an ARM64 job and produce a wrong artifact — the false PASS, not the queue delay, that
            # this file's header forbids. It also diverges from the sibling writer of the same
            # invariant, `tools/runner_autoscale.py::_preserve_labels`, which drops the WHOLE
            # family on every arch ("it names an image, not an architecture") — and a keep and a
            # drop on the same label is the divergence that function's own comment forbids. A
            # repair UNIONS, so a kept pin is permanent until someone runs an explicit `off` repair:
            # the narrowing has to happen HERE or not at all. Anything else in the family is
            # reported on the `not kept=` / `removed=` lines, never silently absorbed.
            if [ "$l" = "$OS_LABEL" ]; then
              pinned_kept="${pinned_kept:+$pinned_kept,}$l"
            else
              pinned_dropped="${pinned_dropped:+$pinned_dropped,}$l"
              say "NOT KEPT: '$l' is a version-pinned hosted label outside this box's own label set ($OS_LABEL is the only one it can claim) — keeping it would make another image's claim on an x86_64 runner. Reported on the removed= line; nothing re-applies it here."
              continue
            fi ;;
          off) pinned_dropped="${pinned_dropped:+$pinned_dropped,}$l"; continue ;;
          *) pinned_dropped="${pinned_dropped:+$pinned_dropped,}$l"
             say "WARNING: CI_RUNNER_OS_LABEL='$CI_RUNNER_OS_LABEL' is not on|auto|off — read as off, so the version pin '$l' is not kept (unreachable: the flag is validated before dispatch)"; continue ;;
        esac
      fi
      list_has "$after" "$l" || after="${after:+$after,}$l"
    done
    # ⛔ THE KEEP-HALF IS ARCH-BLIND WITHOUT THIS GUARD, which re-creates #6897's own defect on the
    # labels it was measured on. The union keeps `ubuntu-latest` ON PRINCIPLE, but on a runner whose
    # read-only host label says ARM64 that keep hands an `ubuntu-latest` job to an arm64 box — the
    # measured failure (job won by the ARM pool, died in "Set up job" on a codeload timeout, one
    # shard per run turned into a FALSE RED per shard) that got the ARM pool stripped in the first
    # place. The repair may be run from any host and against any install, so the runner's own
    # `ARM64`/`ARM` label is the one witness a caller cannot talk it out of: this runner is refused
    # (rc 3, nothing written) rather than PUT `self-hosted-x64` and `ubuntu-latest` onto an arm64
    # registration. An absent arch label keeps the pre-existing path — the API always returns one.
    case "$host_arch" in
      ARM|ARM64) say "WARNING: $agent reports the read-only host label '$host_arch', so it is not an x86_64 runner: this repair would add $ARCH_LABEL to it and KEEP $FALLBACK_LABEL there, which is the #6897 false red (an arm64 box wins a $FALLBACK_LABEL job and dies in Set up job). Nothing was changed for this runner — relabel it from the x86_64 host that owns it, or deregister it."; rc=3; continue ;;
    esac
    for l in $(printf '%s' "$LABELS" | tr ',' ' '); do
      list_has "$after" "$l" || after="${after:+$after,}$l"
    done
    # DERIVED, never asserted: what the PUT would REMOVE, computed from the two sets alone. A union
    # removes nothing EXCEPT the two carved-out classes above, so a value here must be one of those
    # and nothing else — printed BEFORE the PUT, which is the whole point (a removal must be visible
    # in the output, not inferred from a job that never appears).
    removed=""
    for l in $(printf '%s' "$current" | tr ',' ' '); do
      is_host_label "$l" && continue
      list_has "$after" "$l" || removed="${removed:+$removed,}$l"
    done
    # A label outside GitHub's own charset would build a malformed JSON body; refuse rather than PUT
    # one — and never skip it, because skipping a CURRENT label IS the removal this block forbids.
    illegal=""
    for l in $(printf '%s' "$after" | tr ',' ' '); do
      case "$l" in *[!A-Za-z0-9._-]*) illegal="$l" ;; esac
    done
    if [ -n "$illegal" ]; then
      say "WARNING: label '$illegal' on $agent is not a legal runner label (alphanumeric, '.', '_', '-') — refusing to build a request body from it. Nothing was changed for this runner."; rc=3; continue
    fi
    # ⚠️ KEPT DELIBERATELY, AND UNREACHABLE BY CONSTRUCTION at this head: the union can only be empty
    # if the COMPUTED set is, and `resolve_labels` always emits at least `$ARCH_LABEL` — the modes are
    # pinned by `test_the_computed_set_is_never_empty_which_is_what_makes_the_empty_union_guard_dead`.
    # It stays because the array it refuses to send is not harmless: the vendor's own Set-custom-labels
    # parameter note reads "you can pass an empty array to remove all custom labels", i.e. exactly the
    # strip this block exists to stop, and the guard costs one line against a future edit to
    # `resolve_labels`.
    if [ -z "$after" ]; then
      say "WARNING: the union of $agent's current labels and the computed set ($LABELS) is EMPTY — refusing to PUT an empty label set, which would strip the runner. Nothing was changed for this runner."; rc=3; continue
    fi
    say "labels for $agent (id $id):"
    say "  before=[$current]"
    say "  after =[$after]"
    say "  host-owned, not sent=[$host_skipped] alien-arch, not kept=[$arch_dropped] version-pinned, not kept=[$pinned_dropped] version-pinned, kept=[$pinned_kept]"
    say "  removed=[$removed]  (CI_RUNNER_OS_LABEL=${CI_RUNNER_OS_LABEL:-off} decides the version-pin carve-out)"
    body="$(printf '{"labels":["%s"]}' "$(printf '%s' "$after" | sed 's/,/","/g')")"
    if [ "$DRY" = 1 ]; then say "dry-run: would PUT $body to runner $id ($agent)"; set +f; continue; fi
    # ⛔ `relabelled` IS A CLAIM ABOUT THE SERVER'S STATE, NOT ABOUT THE PUT'S EXIT STATUS. A whole-set
    # relabel on this pool has already printed `set_labels ok=16 fail=0` while the label it reported
    # attaching was still absent (#6897) — an exit status says a request was accepted, never that it
    # took effect, and the two differ exactly when the repair matters. The Set verb's own 200 carries
    # the runner's RESULTING label set, so it is read back through `--jq` and every label this repair
    # asked for must be in it; anything else is exit 3 with the returned set in the message, never a
    # `relabelled` line over a state nobody looked at.
    if ! relabelled_now="$(printf '%s' "$body" | gh api -X PUT "repos/$REPO/actions/runners/$id/labels" --input - --jq '[.labels[].name]|join(",")' 2>/dev/null)"; then
      say "WARNING: relabel FAILED for $agent (id $id)"; rc=3; continue
    fi
    missing=""
    for l in $(printf '%s' "$after" | tr ',' ' '); do
      list_has "$relabelled_now" "$l" || missing="${missing:+$missing,}$l"
    done
    set +f
    if [ -n "$missing" ]; then
      say "WARNING: the PUT for $agent (id $id) was ACCEPTED but the label set GitHub returned does not carry $missing (returned=[$relabelled_now]) — the write did NOT take effect (#6897 measured ok=16 fail=0 for a label that was never attached). Nothing is claimed about this runner; re-read it with --status before trusting the queue."; rc=3; continue
    fi
    say "relabelled $agent → $after (read back from the PUT's own response)"
  done
  exit "$rc"
fi

# ── labels question: resolve and print, with NO guard, NO token, NO network ────
# This is the seam the invariant is tested through. It runs the SAME `resolve_labels` the install
# path feeds to config.sh, so a wrong label here is a wrong label there.
# ⛔ IT SITS BEFORE THE ARCH GUARD AND BEFORE EVERY HEADROOM GUARD. The earlier placement (after
# them) made `--print-labels` exit 2 on any non-x86_64 host — the label invariant hard-FAILED on
# the Apple-Silicon box this fleet runs on, and on the five ARM64 runners that serve this very
# pool whenever the fallback is engaged. Asking "which labels would a runner
# get" depends on no host property: the arch refusal is an INSTALL guard, and it still refuses
# every install.
if [ "$MODE" = labels ]; then
  DRY=1   # auto resolves to "decide at install" rather than reading the pool
  resolve_labels
  printf '%s\n' "$LABELS"
  exit 0
fi

# ── guards: measured headroom, in the harness's own order (forks, disk, load) ──
# ⛔ THE ARCH REFUSAL IS AN **INSTALL** GUARD — and it sat ABOVE the uninstall block, so on any
# non-x86_64 host `--uninstall` was refused (exit 2) and nothing was removed: a runner could not
# be decommissioned from the box it was installed on, and the runbook said the opposite ("still
# governs every INSTALL"). Only INSTALLING registers a runner with THIS arch's label
# (`self-hosted-x64`); `--status`, `--repair-labels` and `--print-labels` reach only `gh` and are
# arch-free. The destructive path is guarded by `is_runner_install`, never by the arch.
#
# ⚠️ BUT DEREGISTRATION IS **NOT** ARCH-FREE, and this line used to say it was. The runner
# package's own `config.sh` execs the listener for BOTH verbs — `./bin/Runner.Listener "$@"` when
# `$1 = remove` — and that binary is the x64 ELF the tarball extracts into `$d/bin`. On a foreign
# arch the exec fails ("Exec format error"); the pre-fix pair discarded it with `>/dev/null 2>&1`,
# and the run cannot deregister: the unit is already gone by then (it is removed first,
# deliberately), so the box is left half-decommissioned — unit gone, registration alive. The
# failed-deregistration branch now CAPTURES that output and appends its last line to the warning.
# Measured on an unpacked `actions-runner-linux-x64` tarball, whose `config.sh:76-81` is
# `if [[ "$1" == "remove" ]]; then ./bin/Runner.Listener "$@"` — the version is a measurement
# detail (this script pins `$RUNNER_VERSION` below and extracts into `$d`, so the installed file is
# `$d/config.sh`); the exec is not. To decommission from a non-x86_64 host: remove the unit here,
# then deregister from an x86_64 host or delete the runner through the API.
if [ "$MODE" = install ]; then
  # `CI_RUNNER_ARCH` is a seam, so the refusal can be exercised on a host of either arch (the
  # guarded box is not always the box reading this code). Nothing here is made safer by it: anyone
  # who can set the env can edit the script.
  arch="${CI_RUNNER_ARCH:-$(uname -m)}"
  [ "$arch" = "x86_64" ] || die "this installer is for x86_64 hosts; '$arch' would register a DIFFERENT arch label than it claims"
  # COUNT is validated once, before the mode dispatch, for every mode — not here (finding 4).

  # FORK HEADROOM FIRST — the failure the harness has hit is fork exhaustion, not CPU.
  procs=$(ps -e 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')
  # `CI_RUNNER_FORK_CAP` is a seam for exercising the `unlimited` branch below on a box that
  # cannot actually raise RLIMIT_NPROC to infinity.
  fork_cap="${CI_RUNNER_FORK_CAP:-$(ulimit -u 2>/dev/null || echo unknown)}"
  threads_max=$(cat /proc/sys/kernel/threads-max 2>/dev/null || echo unknown)
  if [ "$fork_cap" = unlimited ]; then
    # RLIMIT_NPROC = infinity is a legitimate reading (systemd/containers set it) and the SAFEST
    # possible one — there is no fork bound to exhaust. Treating it as "an unread bound" refused
    # a host that cannot fail this way at all (an over-block, not a fail-closed).
    say "headroom: forks unbounded (ulimit -u=unlimited, $procs now, threads-max $threads_max)"
  else
    case "$fork_cap" in
      ''|*[!0-9]*) die "cannot read the fork cap (ulimit -u='$fork_cap') — refusing to size against an unread bound" ;;
    esac
    need_forks=$(( COUNT * FORKS_PER_RUNNER + procs ))
    [ "$need_forks" -le "$fork_cap" ] || die "fork headroom: $COUNT runners need ~$need_forks processes against ulimit -u=$fork_cap (now $procs)"
    say "headroom: forks ok ($procs now, ~$need_forks needed, cap $fork_cap, threads-max $threads_max)"
  fi

  # DISK NEXT — a full disk fails a job as a red, not as a queue delay.
  disk_free_gb=$(df -Pk "$(dirname "$BASE")" 2>/dev/null | awk 'NR==2{printf "%d", $4/1024/1024}')
  case "$disk_free_gb" in ''|*[!0-9]*) die "cannot read free disk space for $(dirname "$BASE")" ;; esac
  disk_need_gb=$(( COUNT * DISK_PER_RUNNER_GB + DISK_RESERVE_GB ))
  [ "$disk_free_gb" -ge "$disk_need_gb" ] || die "disk headroom: ${disk_free_gb}G free against ${disk_need_gb}G needed for $COUNT runners (+${DISK_RESERVE_GB}G reserve)"
  say "headroom: disk ok (${disk_free_gb}G free, ${disk_need_gb}G needed)"

  # CPU LAST — a CAPACITY bound, not a live-load gate: the agent lanes are bursty, so a momentary
  # load reading must never be what decides whether CI gets capacity. `CI_RUNNER_NCPU` pins the
  # core count so the arithmetic can be exercised on a machine of any size.
  ncpu="${CI_RUNNER_NCPU:-$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo '')}"
  case "$ncpu" in ''|*[!0-9]*) die "cannot read the core count (nproc/getconf _NPROCESSORS_ONLN both failed) — refusing to size against an unread bound" ;; esac
  cpu_need=$(( COUNT * CORES_PER_RUNNER + CORES_RESERVED_FOR_AGENT_LANES ))
  [ "$cpu_need" -le "$ncpu" ] || die "cpu headroom: $COUNT runners need ${COUNT}x${CORES_PER_RUNNER} cores + ${CORES_RESERVED_FOR_AGENT_LANES} reserved for agent lanes = $cpu_need, this box has $ncpu (measured ~1.0-1.5 cores per concurrent job)"
  # ⚠️ THE MEMORY LEG IS THE ONE LIVE READING (MemAvailable), and the runbook says so: unlike the
  # cpu capacity bound it CAN refuse on a loaded box (measured 2026-10-22: 6G available refused
  # `--count 2`, which needs 8G). That is deliberate fail-closed — two runners starting into 6G
  # would OOM-kill the agent lanes' FalkorDB — but it must not be described as a capacity bound.
  mem_free_gb=$(awk '/MemAvailable/{printf "%d", $2/1024/1024}' /proc/meminfo 2>/dev/null)
  mem_need_gb=$(( COUNT * MEM_PER_RUNNER_GB + MEM_RESERVE_GB ))
  [ "${mem_free_gb:-0}" -ge "$mem_need_gb" ] || die "memory headroom: ${mem_free_gb:-0}G available against ${mem_need_gb}G needed for $COUNT runners (+${MEM_RESERVE_GB}G reserve)"
  say "headroom: cpu ok (${COUNT}x${CORES_PER_RUNNER} + ${CORES_RESERVED_FOR_AGENT_LANES} reserved = ${cpu_need} of ${ncpu} cores; ${mem_free_gb:-0}G mem free, ${mem_need_gb}G needed)"
fi

# ── uninstall ─────────────────────────────────────────────────────────────────
if [ "$MODE" = uninstall ]; then
  need gh
  # ⛔ EVERY TARGET IS VALIDATED BEFORE ANYTHING IS DELETED. `BASE` is operator-overridable
  # (`CI_RUNNER_BASE`), so "a directory exists at this path" is NOT evidence of a runner: the
  # pre-fix gate was exactly `[ -d "$d" ]`, so `CI_RUNNER_BASE=/opt --uninstall` ran
  # `sudo rm -rf /opt` and `CI_RUNNER_BASE=$HOME/work --uninstall` deleted a worktree. Proof of
  # a runner install is `.runner` (written by `config.sh` on success) or the listener binary —
  # a lone `config.sh` is NOT proof (see `is_runner_install`; it is a common filename, and a bare
  # script that exits 0 satisfies `./config.sh remove`, which is the precondition for the delete).
  #
  # ⛔ TWO PHASES, AND EVERYTHING THAT CAN REFUSE HAPPENS IN THE FIRST ONE. Phase 1 walks every
  # target and proves it is a runner install; phase 2 mints a removal token for EVERY target and
  # only then starts deregistering. The mint used to sit INSIDE the deleting loop, so a mint that
  # failed on runner 2 `die`d after runner 1 was already deregistered and deleted — a half-deleted
  # set, which an earlier version of this comment claimed was impossible. Now a refusal in either
  # phase leaves EVERY directory untouched.
  uninstall_dirs=()
  uninstall_units=()
  orphan_units=()
  rc=0
  for i in $(seq 1 "$COUNT"); do
    d="$(runner_dir "$i")"
    unit="$(unit_for "$NAME_PREFIX-$i")"
    if [ ! -d "$d" ]; then
      # ⛔ A UNIT OUTLIVES ITS DIRECTORY, AND ITS NAME NEEDS NO DIRECTORY. The unit of this instance
      # is derived from its NUMBER, so it is readable here; skipping the whole instance on a missing
      # directory is how `--uninstall` printed `nothing to uninstall` and exited 0 while an ENABLED
      # unit kept restarting into a WorkingDirectory that no longer existed — the state this block
      # exists to prevent. With no `$d/svc.sh` it cannot be uninstalled from here, so it is REPORTED
      # and the run exits 3: a caller must never read "ok" for a box that still runs the service.
      [ -e "$UNIT_DIR/$unit" ] && orphan_units+=("$unit")
      say "skip: $d does not exist"
      continue
    fi
    if ! is_runner_install "$d"; then
      die "$d is not a runner install (no .runner and no bin/Runner.Listener) — refusing to delete it. Remove it by hand if it is debris, or pass the right CI_RUNNER_BASE."
    fi
    uninstall_dirs+=("$d")
    # the unit name comes from the INSTANCE NUMBER, not from the array index: instance 1 may be
    # absent while 2 exists, and deriving it from the index would then address the wrong unit.
    uninstall_units+=("$unit")
  done
  if [ "${#orphan_units[@]}" -gt 0 ]; then
    for u in "${orphan_units[@]}"; do
      say "WARNING: $u is still installed but its runner directory is gone, so there is no svc.sh here to uninstall it with — remove it by hand: sudo systemctl disable --now $u && sudo rm -f $UNIT_DIR/$u"; rc=3
    done
  fi
  if [ "${#uninstall_dirs[@]}" -eq 0 ]; then
    [ "${#orphan_units[@]}" -eq 0 ] && say "nothing to uninstall"
    exit "$rc"
  fi
  uninstall_tokens=()
  if [ "$DRY" != 1 ]; then
    for d in "${uninstall_dirs[@]}"; do
      tok="$(gh api -X POST "repos/$REPO/actions/runners/remove-token" --jq .token)" \
        || die "could not mint a removal token for $(basename "$d") — NOTHING was deregistered or deleted; re-run once the token request succeeds" 3
      uninstall_tokens+=("$tok")
    done
  fi
  for i in "${!uninstall_dirs[@]}"; do
    d="${uninstall_dirs[$i]}"; name="$(basename "$d")"; unit="${uninstall_units[$i]}"
    if [ "$DRY" = 1 ]; then say "dry-run: would stop and uninstall the unit $unit, deregister $name, then remove $d"; continue; fi
    # ⛔ THE UNIT GOES FIRST, AND ITS FAILURE STOPS THE DELETE FOR THIS TARGET. The pre-fix line was
    # `[ -f ./svc.sh ] && sudo ./svc.sh stop >/dev/null 2>&1; sudo ./svc.sh uninstall >/dev/null 2>&1`:
    # the `-f` guard covered `stop` only, `uninstall` therefore ran even with no svc.sh (where it can
    # only fail), its exit status was discarded, and `sudo rm -rf` ran regardless. An enabled unit
    # then survived pointing at a deleted WorkingDirectory — systemd restarts it into nothing — while
    # `--uninstall` and `--status` both reported a clean removal. One target that cannot be cleaned is
    # left INTACT and reported (rc=3 — an OPERATION failure, never 2 "nothing attempted" and never 1
    # "usage"), so a retry has something to retry.
    if [ -f "$d/svc.sh" ]; then
      ( cd "$d" && sudo ./svc.sh stop >/dev/null 2>&1 )
      if ! ( cd "$d" && sudo ./svc.sh uninstall >/dev/null 2>&1 ) && [ -e "$UNIT_DIR/$unit" ]; then
        say "WARNING: could not remove the systemd unit $unit — LEFT $d and its registration intact so this can be retried. Remove it by hand: sudo $d/svc.sh uninstall"; rc=3; continue
      fi
      say "removed the systemd unit $unit (if it was installed)"
    elif [ -e "$UNIT_DIR/$unit" ]; then
      say "WARNING: $unit is installed but $d has no svc.sh, so it cannot be uninstalled from here — LEFT the directory and its registration intact. Remove the unit by hand, then re-run"; rc=3; continue
    fi
    tok="${uninstall_tokens[$i]}"
    # ⛔ A FAILED DEREGISTRATION IS AN OPERATION FAILURE, AND IT STOPS THE DELETE FOR THIS TARGET —
    # the same rule as the unit branch above, and the one remaining hole in this loop. The pre-fix
    # pair discarded the status (`&& say` / `|| say`) and deleted `$d` unconditionally under it, so a
    # runner the repo still lists lost the ONLY state a retry can use (`config.sh` and `.runner` live
    # in `$d`), the re-run then reported "nothing to uninstall", and the caller was told `0` — against
    # this file's own EXIT contract (`3 OPERATION failed — … config.sh …`) and the runbook's copy of
    # it. Leave the target INTACT, report it, and let the retry retry.
    if out="$( cd "$d" && ./config.sh remove --token "$tok" --unattended 2>&1 )"; then
      say "deregistered $name"
    else
      # ⚠️ THE CAPTURED OUTPUT IS THE ONLY WAY TO TELL THE TWO CAUSES APART, and the second one is
      # not retryable: `config.sh remove` execs `./bin/Runner.Listener`, an x86-64 ELF, so on a
      # foreign arch it dies with an exec-format error no re-run on that host can fix. Swallowing the
      # output (`>/dev/null 2>&1`) turned both causes into the same "check --status" sentence.
      last="$(printf '%s\n' "$out" | grep -v '^[[:space:]]*$' | tail -1)"
      say "WARNING: could not deregister $name — LEFT $d and its registration intact so this can be retried; it may still be listed in the repo, check --status${last:+ (last output: $last)}"; rc=3; continue
    fi
    if ! sudo rm -rf "$d"; then
      # ⛔ THE DELETE IS THE LAST DESTRUCTIVE STEP AND ITS STATUS IS THE ONE THAT USED TO BE
      # DISCARDED. `set -e` is off and `rc` was untouched, so a `sudo rm` that failed reported
      # `deregistered` + exit 0 while the directory survived — the same fail-open shape the unit
      # and deregistration branches above were fixed for. The registration IS gone now, so a re-run
      # finds the directory, fails to deregister it, and exits 3: say so here, while the cause is
      # still the removal and not a phantom registration.
      say "WARNING: could not remove $d — it IS deregistered but still on disk; remove it by hand (a re-run would find nothing to deregister and fail)"; rc=3
    fi
  done
  exit "$rc"
fi

tarball="actions-runner-linux-x64-$RUNNER_VERSION.tar.gz"
url="https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/$tarball"
actor="${GITHUB_ACTOR:-cmux}"

# ⛔ `need gh` BEFORE the fallback read, on the non-dry path only. `resolve_labels`' `auto` branch
# asks `fallback_engaged` first, and with `gh` absent its command-not-found text was captured as an
# unreadable state — so a missing tool REFUSED with exit 2 ("GUARD refused, nothing attempted")
# while the EXIT contract above promises 3 for "a tool missing from PATH". A dry run keeps working
# with no gh: its label path short-circuits before any read.
if [ "$DRY" != 1 ]; then need gh; fi
resolve_labels
for t in $JOB_TOOLS $RUNNER_TOOLS; do
  command -v "$t" >/dev/null 2>&1 || say "WARNING: '$t' is not on PATH — jobs needing it will exit 2 (#9018)"
done

for i in $(seq 1 "$COUNT"); do
  d="$(runner_dir "$i")"; name="$NAME_PREFIX-$i"
  say "── runner $i: name=$name dir=$d labels=$LABELS"
  if [ "$DRY" = 1 ]; then say "dry-run: would download $url, verify sha256, then run config.sh --unattended --url https://github.com/$REPO --token <minted> --name $name --labels $LABELS --work _work --replace, then svc.sh install+start"; continue; fi
  need gh; need curl; need tar; need sha256sum

  if [ ! -f "$d/.runner" ]; then
    sudo mkdir -p "$d" && sudo chown "$(id -un):$(id -gn)" "$d" || die "cannot create $d" 3
    say "downloading $url"
    curl -fsSL --max-time 300 -o "$d/$tarball" "$url" || die "download failed" 3
    got="$(sha256sum "$d/$tarball" | cut -d' ' -f1)"
    [ "$got" = "$SHA256_LINUX_X64" ] || { rm -f "$d/$tarball"; die "tarball sha256 mismatch: got $got, expected $SHA256_LINUX_X64 — refusing to execute it" 3; }
    say "sha256 verified ($got)"
    tar xzf "$d/$tarball" -C "$d" && rm -f "$d/$tarball" || die "extract failed in $d" 3
    tok="$(gh api -X POST "repos/$REPO/actions/runners/registration-token" --jq .token)" || die "could not mint a registration token" 3
    # ⛔ ONE ARGV, BUILT ONCE — and the plan above prints the SAME argv (`--name`, `--work` and
    # `--replace` included, token elided), so what a dry run shows is what config.sh receives. A
    # narrower plan is how `--replace` — the flag that takes over an existing registration of the
    # same name — stayed invisible to the one mode whose job is to show it.
    set -- --unattended --url "https://github.com/$REPO" --token "$tok" \
           --name "$name" --labels "$LABELS" --work _work --replace
    ( cd "$d" && ./config.sh "$@" ) || die "config.sh failed for $name" 3
  else
    say "already configured ($d/.runner present) — reusing (use --repair-labels to re-sync labels)"
  fi

  # systemd (system mode): this is what makes it survive a reboot. `svc.sh install <user>`
  # writes /etc/systemd/system/<unit>.service; it runs only when the unit is ABSENT, and it is
  # NOT `systemctl enable` — a unit removed while its dir survives is re-installed here, but a
  # merely-STOPPED service is only started below (see the runbook's reboot section).
  if [ ! -f "$UNIT_DIR/$(unit_for "$name")" ]; then
    ( cd "$d" && sudo ./svc.sh install "$actor" ) || die "svc.sh install failed for $name" 3
  fi
  ( cd "$d" && sudo ./svc.sh start ) || die "svc.sh start failed for $name" 3
  say "started $name"
done

say ""
say "verify:"
say "  gh api repos/$REPO/actions/runners --jq '.runners[]|select(.name|startswith(\"$NAME_PREFIX\"))|{name,status,busy,labels:[.labels[].name]}'"
say "  tools/install-ci-runner-x64.sh --status"
exit 0
