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
#   — never stamping `self-hosted-arm64`. The label stays gate-following; see `--repair-labels`.
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
#   tools/install-ci-runner-x64.sh --repair-labels --count 2  # re-sync labels to the gate's state
#   tools/install-ci-runner-x64.sh --status                   # remote view + local units
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
#     2  GUARD refused — headroom / arch / undecidable fallback / a `--uninstall` target that is
#        not provably a runner install. Nothing was downloaded, registered or removed; re-run
#        with a smaller --count, on an x86_64 host, with an explicit --fallback, or with the
#        right CI_RUNNER_BASE.
#     3  OPERATION failed — download, sha256 verify, extract, token mint, config.sh, svc.sh (a
#        failed systemd unit removal, or a failed `config.sh remove`, included — that target is then
#        LEFT intact rather than deleted out from under an enabled unit or a live registration), a
#        tool missing from PATH, or a failed remote read (`--status`).
#     4  label-coverage could not read the workflows, so it has NO verdict — run `--status` from
#        the repo root. An empty read is not a clean bill of health.
#     5  label-coverage WAS read and found a required label with NO self-hosted answer — the
#        incident state (every PR blocked). 0 does not mean "covered": read the report.
set -uo pipefail

# The help text is the file's OWN header, cut at the closing `set` line rather than a frozen line
# range: a range stops matching the moment a USAGE row is inserted above it, and it then silently
# truncates `--uninstall`, `--fallback`, the REBOOT note and the EXIT contract (which is what
# `sed -n '1,44p'` did).
usage() { awk '/^# USAGE/{p=1} /^set -uo pipefail$/{p=0} p' "$0" | sed 's/^# \{0,1\}//'; }

REPO="${CI_RUNNER_REPO:-daniel-ospina/tortoise}"
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
  # ⛔ ONE PAGINATED READ OF (name, labels), NOT A ONE-PAGE LABEL TALLY. `per_page=100` with no
  # `--paginate` silently truncated a pool larger than 100 and reported a covered label as `0
  # runners` (a false red on a large pool) — the read directly above it (`fallback_engaged`) is
  # paginated for the same reason. The runner COUNT comes out of the same read, which is what lets
  # the partial-coverage arm below exist at all: a label tally cannot tell 12 runners from 1.
  pool_tsv="$(gh api --paginate "repos/$REPO/actions/runners" --jq '.runners[] | [.name, ([.labels[].name]|join(","))] | @tsv' 2>/dev/null)" || exit 3
  # ⛔ THE RUNNER COUNT IS A MEASUREMENT, AND IT IS THE DENOMINATOR — so it is PASSED to the awk
  # below, not re-inferred there. `awk` counts RECORDS, and an empty pool still feeds it ONE empty
  # record (`printf '%s\n' ""`), so `total` came out as 1 and an empty pool printed
  # `0 of 1 self-hosted runner(s)` — a fabricated runner, in the one block whose doctrine is "an
  # empty read is not a coverage verdict". The correct 0 was computed here and then never read.
  pool_total="$(printf '%s\n' "$pool_tsv" | grep -c . || true)"
  case "$pool_total" in ''|*[!0-9]*) pool_total=0 ;; esac
  uncovered=0
  labels_seen=0
  # ⛔ A WHOLE-LINE YAML COMMENT IS NOT A `runs-on:` LINE. Unanchored, `# legacy: runs-on: windows-2022`
  # was counted as a required label and printed a false ⛔ UNCOVERED, and this tree's own comments
  # name both real labels — the block printed 85/12 where the real lines are 77/9 (2026-10-29).
  # Both arms are needed: strip whole-line comments, AND anchor to the line's first token — anchoring
  # is what stops a TRAILING comment (`runs-on: x  # note: runs-on: y`) contributing `y`.
  # ⛔ AND `*.yaml` IS AS REAL AS `*.yml` — GitHub accepts both. Globbing only `*.yml` read NOTHING
  # on a repo whose workflows are `.yaml`, which the empty-read arm below then reports as exit 4 (a
  # false failure, not a false pass) while `--status` never sees the labels it exists to check.
  for label in $(grep -rhvE '^[[:space:]]*#' .github/workflows/*.yml .github/workflows/*.yaml 2>/dev/null \
      | grep -oE '^[[:space:]]*runs-on:[[:space:]]*(\[[^]]*\]|[A-Za-z0-9._/-]+)' \
      | sed -E 's/^[[:space:]]*runs-on:[[:space:]]*//; s/^\[//; s/\]$//' | tr ',' '\n' \
      | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//; s/^"//; s/"$//' \
      | grep -vE '^(\$|[[:space:]]*$)' | sort -u); do
    labels_seen=$((labels_seen + 1))
    # ⛔ n OF WHO? The denominator has to be the runners that COULD answer this label, or the
    # reading is a false red on a two-arch pool: the ARM64 runners must never claim the bare
    # `ubuntu-<version>` (that is the arch invariant `_preserve_labels` now enforces), so
    # `7 of 12` on the live pool would read as erosion when it is exactly correct. A bare version
    # label is measured against the x64 pool, an `-arm` spelling against the arm64 pool, and
    # anything else against the whole pool.
    counts="$(printf '%s\n' "$pool_tsv" | awk -F'\t' -v want="$label" -v x64label="$ARCH_LABEL" -v armlabel="$ARM_POOL_LABEL" \
      -v pool_total="$pool_total" '
      {k=split($2,a,","); hit=0; x64=0; arm=0
       for(i=1;i<=k;i++){ if(a[i]==want) hit=1; if(a[i]==x64label) x64=1; if(a[i]==armlabel) arm=1 }
       if (hit) n++; if (x64) nx64++; if (arm) narm++}
      END { total = pool_total + 0   # the MEASURED runner count, never the record count above
            denom = total
            if (want ~ /^ubuntu-[0-9][0-9.]*-arm$/) denom = narm
            else if (want ~ /^ubuntu-[0-9][0-9.]*$/) denom = nx64
            if (denom == 0) denom = total
            printf "%d %d", n+0, denom+0 }')"
    n="${counts% *}"
    denom="${counts#* }"
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
      say "  ⛔ $label = 0 of $denom self-hosted runner(s) answer it."
      case "$label" in
        *-arm)
          say "     An ARM64 hosted spelling — not this pool's class on an x64 box (a false architecture claim). A report, not a block."
          ;;
        "$FALLBACK_LABEL")
          say "     GitHub-hosted serves $FALLBACK_LABEL while the gate is not routing it here — a report, not a block."
          ;;
        ubuntu-[0-9]*)
          say "     A required leg naming it can then only be served by GitHub-hosted: python-ci-gate is never dispatched and every PR stays BLOCKED."
          uncovered=1
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
      say "  $label = $n of $denom runner(s) — ⚠️ PARTIAL: the other $((denom - n)) cannot answer it. A re-registration or fill that omits it erodes coverage silently; re-apply with CI_RUNNER_OS_LABEL=on --repair-labels."
    else
      say "  $label = $n of $denom runner(s)"
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
    say "⛔ UNCOVERED label(s) above. To (re-)apply this script's label set INCLUDING $OS_LABEL, run:"
    say "     CI_RUNNER_OS_LABEL=on $0 --repair-labels --count <N>"
    say "   Run it with the variable UNSET and the PUT body has no $OS_LABEL in it — that REMOVES the mitigation from every runner it touches."
  fi
  # ⛔ "NOTHING TO CHECK" IS NOT "ALL COVERED". Run from a directory with no .github/workflows (or on
  # a repo whose workflows name no single-label runs-on) the loop above prints its header, zero rows,
  # and exits 0 — a clean bill of health from the one instrument meant to prove coverage. Refuse
  # instead, with its own exit code so a caller can tell it apart from a real zero-answer label.
  # ⛔ EACH GLOB IS TESTED SEPARATELY: `ls *.yml *.yaml` exits 2 when only ONE of them matches, so a
  # repo with valid `.yaml` workflows and no `.yml` reads as "nothing there" and exits 4.
  if [ ! -d .github/workflows ] \
      || { ! ls .github/workflows/*.yml >/dev/null 2>&1 && ! ls .github/workflows/*.yaml >/dev/null 2>&1; }; then
    say "⛔ label-coverage read NOTHING: no .github/workflows/*.yml|*.yaml in $(pwd) — run --status from the repo root. An empty read is not a coverage verdict."
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
  # ⛔ THE INCIDENT STATE IS NOT SUCCESS. Exit 0 used to cover BOTH "every label covered" and "a
  # required label has no runner at all" — so `--status && …` could not tell the state that blocks
  # every PR from the one that does not. 4 already means "no verdict"; 5 is "a verdict, and it is
  # bad". Nothing in the tree keys on `--status`'s status today (`grep -rn -- '--status'` finds only
  # this script's own help rows), so no caller changes meaning.
  if [ "$uncovered" = 1 ]; then exit 5; fi
  exit 0
fi

# ── repair: re-apply the gate-following label set to already-registered runners ──
# This is the documented repair when the pre-#2327 gate has relabelled these runners: it puts
# back the arch marker and syncs the fallback label to the gate's state, without re-registering.
if [ "$MODE" = repair ]; then
  need gh
  resolve_labels
  rc=0
  for i in $(seq 1 "$COUNT"); do
    d="$(runner_dir "$i")"
    [ -f "$d/.runner" ] || { say "skip: $d is not configured"; continue; }
    agent="$(sed -n 's/.*"agentName": "\([^"]*\)".*/\1/p' "$d/.runner")"
    id="$(gh api --paginate "repos/$REPO/actions/runners" --jq ".runners[]|select(.name==\"$agent\")|.id" | head -1)"
    # An operation that was ATTEMPTED and failed is 3, never 1: the EXIT contract above reserves 1
    # for "usage — nothing was attempted", and a caller keying on it would read a failed relabel as a
    # bad flag (measured: `--repair-labels` against a `gh` that returns no id exited 1).
    case "$id" in ''|*[!0-9]*) say "WARNING: $agent not found in the repo's runners"; rc=3; continue ;; esac
    body="$(printf '{"labels":["%s"]}' "$(printf '%s' "$LABELS" | sed 's/,/","/g')")"
    if [ "$DRY" = 1 ]; then say "dry-run: would PUT $body to runner $id ($agent)"; continue; fi
    if printf '%s' "$body" | gh api -X PUT "repos/$REPO/actions/runners/$id/labels" --input - >/dev/null 2>&1; then
      say "relabelled $agent → $LABELS"
    else
      say "WARNING: relabel FAILED for $agent (id $id)"; rc=3
    fi
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
