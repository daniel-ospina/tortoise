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
#   only under `CI_RUNNER_OS_LABEL=off`/unset (KEPT under `on|auto`). Both are PRINTED, never
#   silently skipped. `_preserve_labels` drops both but reads NO flag: ONE writer here (#10275).
#   `ubuntu-latest` is in neither class (it carries no version) and is always kept.
#   ⚠️ THAT FLAG IS READ HERE AND NOWHERE ELSE IN THIS TREE, and nothing WRITES it: the install-side
#   reader/emitter ships with #10275, still open. So `CI_RUNNER_OS_LABEL` unset — nothing in this
#   tree exports it — takes the `off` arm and DROPS the pin. A repair with no exported flag therefore
#   IS #6897's recorded revert (`CI_RUNNER_OS_LABEL=off` **plus** `--repair-labels`), not a no-op: it
#   prints `version-pinned, not kept=[…]` BEFORE the PUT. Export `CI_RUNNER_OS_LABEL=on` to KEEP one.
#
# ⛔ ARCH LABELS ARE NOT INTERCHANGEABLE. A runner is registered with `self-hosted-x64`
#   (its own distinct pool marker, mirroring `self-hosted-arm64`) — NEVER with
#   `self-hosted-arm64`, and never with `ubuntu-<version>`. Claiming another arch's label
#   would let an x86 box serve an arch-specific job and produce a wrong artifact, which is a
#   false PASS rather than a queue delay. `ubuntu-<version>` stays claimed by NO self-hosted
#   runner: it is the version-pinned HOSTED label that `test-slow` / `workflow-lint` /
#   `finding-provenance` / `mirror-images` are pinned to on purpose (they need the hosted
#   class or a fresh egress IP).
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
set -uo pipefail

# The help text is the file's OWN header, cut at the closing `set` line rather than a frozen line
# range: a range stops matching the moment a USAGE row is inserted above it, and it then silently
# truncates `--uninstall`, `--fallback`, the REBOOT note and the EXIT contract (which is what
# `sed -n '1,44p'` did).
usage() { awk '/^# USAGE/{p=1} /^set -uo pipefail$/{p=0} p' "$0" | sed 's/^# \{0,1\}//'; }

REPO="${CI_RUNNER_REPO:-daniel-ospina/tortoise}"
# The checkout this script lives in. `--status`'s label-coverage reads `.github/workflows` from
# HERE, never from the caller's cwd — an operator runs this from anywhere.
SELF_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
REPO_ROOT_DIR="$(cd "$SELF_DIR/.." 2>/dev/null && pwd)"
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

resolve_labels() { # sets LABELS; exit 2 when the state cannot be read
  local engaged
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
        LABELS="$ARCH_LABEL"
        say "fallback: auto — decided at install time from the ARM64 pool's state (not read in a dry run)"
        return 0
      fi
      engaged="$(fallback_engaged)" || die "cannot read whether the ARM64 pool is carrying $FALLBACK_LABEL — refusing to guess which pool this runner joins. Re-run with --fallback on or --fallback off." 2
      if [ "$engaged" = on ]; then
        LABELS="$ARCH_LABEL,$FALLBACK_LABEL"
        say "fallback: ENGAGED on the ARM64 pool now → labels=$LABELS"
      else
        LABELS="$ARCH_LABEL"
        say "fallback: not engaged on the ARM64 pool now → labels=$LABELS (the gate attaches $FALLBACK_LABEL when it falls back)"
      fi ;;
    *) die "--fallback must be auto|on|off (got '$FALLBACK')" 1 ;;
  esac
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
# `:67-74`, and the pre-#2327 stamp a repair exists to heal. `--repair-labels` therefore always
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
# claims `ubuntu-latest` and NEVER an `ubuntu-<version>` (`docs/ops/ci-runner-x64.md:61`) — that is
# the label a leg pins to escape this pool; `ubuntu-2204-tools` is NOT in the class (boundary test in
# `tests/test_runner_autoscale.py`), hence both ends anchored. ⛔ A HERE-STRING, never a builtin
# writer piped into a quiet grep (#841): under `pipefail` the quiet grep's early exit leaves the
# writer with SIGPIPE and the PIPELINE non-zero over text that DID match — a false negative here would drop this carve-out and put a version pin back on the pool. `repo-harnesses` enforces the shape.
is_version_pinned_hosted_label() {
  grep -qiE '^ubuntu-[0-9]+(\.[0-9]+)*(-arm)?$' <<<"$1"
}

# ── label coverage: which `runs-on:` labels does the pool actually answer? ────
# A `runs-on:` label that NO runner answers does not fail — it does not run. GitHub leaves the run
# `pending` with ZERO materialised jobs, so there is no red check and no green one: the required
# aggregate simply never appears and every PR is blocked with nothing to look at (measured
# 2026-10-27 on `python-ci-gate`, the repo's ONLY required condition: runs 40898791192 and
# 40901844458, status `pending`, jobs=0, the check absent from every PR head). This block exists so
# that state is READ instead of inferred from a silent queue.
#
# ⚠️ A ZERO IS NOT ALWAYS A FAULT — a hosted label (`ubuntu-latest`, `ubuntu-24.04`) is normally
# served by GitHub-hosted capacity, so 0 self-hosted answers is the healthy case on a hosted day.
# It is still printed loudly, because when hosted capacity does not materialise the failure is
# completely silent, and the operator's next question is exactly "who answers this label?".
# ── the workflow files this reader may read ─────────────────────────────────
# GitHub accepts BOTH `.yml` and `.yaml`. Globbing only `*.yml` silently dropped a `.yaml`
# workflow, so the coverage block below printed "every one has an online self-hosted answer" over a
# file it never read — the same false-clean one level down that this whole block exists to expose.
# ⛔ EXISTING FILES ONLY, and that is not cosmetic: `set -o pipefail` is on (`:106`) and a glob that
# matches nothing is passed through LITERALLY, so handing `…/*.yaml` to grep makes grep exit 2 on a
# missing path and the whole read report "cannot read" on a tree that HAS workflows (measured while
# fixing this: every label-coverage arm went SKIPPED). One path per line; empty when there are none.
workflow_files() {
  local wf="$REPO_ROOT_DIR/.github/workflows" f
  [ -d "$wf" ] || return 1
  for f in "$wf"/*.yml "$wf"/*.yaml; do
    if [ -f "$f" ]; then printf '%s\n' "$f"; fi
  done
}

workflow_labels() { # the distinct `runs-on:` scalars the workflows use, one per line
  local files f
  files="$(workflow_files)" || return 1
  [ -n "$files" ] || return 1
  # `|| true`: a file with no `runs-on:` line makes grep exit 1, and under pipefail that would fail
  # the whole read instead of contributing nothing.
  while IFS= read -r f; do
    grep -hE '^[[:space:]]*runs-on:' "$f" 2>/dev/null || true
  done <<<"$files" \
    | sed -E 's/^[[:space:]]*runs-on:[[:space:]]*//; s/[[:space:]]+#.*$//' \
    | tr -d '"' | tr -d "'" | tr -d ' ' \
    | tr ',' '\n' | sed -E 's/^\[//; s/\]$//' \
    | sed '/^$/d' | sort -u
}

# How many `runs-on:` keys are written as a YAML BLOCK LIST (`runs-on:` then `- a` lines) — a shape
# this scalar reader cannot see, counted so the report can SAY so instead of reading as complete.
unreadable_runs_on_keys() {
  local files f n=0 c
  files="$(workflow_files)" || { printf '0'; return 0; }
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    c="$(grep -cE '^[[:space:]]*runs-on:[[:space:]]*(#.*)?$' "$f" 2>/dev/null || true)"
    case "$c" in ''|*[!0-9]*) c=0 ;; esac
    n=$((n + c))
  done <<<"$files"
  printf '%d' "$n"
}

label_coverage() { # $1 = the runner rows (name<TAB>status<TAB>busy=…<TAB>label,label,…)
  local rows="$1" labels label pair on off n=0 zero="" wf="$REPO_ROOT_DIR/.github/workflows" unreadable
  if ! labels="$(workflow_labels)" || [ -z "$labels" ]; then
    say "label-coverage: cannot read a \`runs-on:\` label from $wf/*.yml or $wf/*.yaml — SKIPPED (this makes NO claim about any label)"
    return 0
  fi
  # A `runs-on:` written as a YAML block list (`runs-on:` then `- a` lines) is not a scalar this
  # reader can see. Say so instead of counting a surface it did not read — an unread label that
  # silently reads as healthy is the failure this whole block exists to expose.
  unreadable="$(unreadable_runs_on_keys)"
  say "label-coverage — answering ONLINE self-hosted runners, per \`runs-on:\` label the workflows use:"
  for label in $labels; do
    # a matrix expression (`${{ … }}`) is not a label; it is resolved per job at run time.
    case "$label" in *'${{'*) continue ;; esac
    n=$((n + 1))
    pair="$(printf '%s\n' "$rows" | awk -F'\t' -v want="$label" '
      NF >= 4 { k = split($4, a, ","); for (i = 1; i <= k; i++) if (a[i] == want) { if ($2 == "online") on++; else off++ } }
      END { printf "%d %d", on + 0, off + 0 }')"
    on="${pair%% *}"; off="${pair##* }"
    say "  $label → $on online, $off offline"
    if [ "$on" -eq 0 ]; then
      zero="${zero:+$zero }$label"
      say "     ⚠️  NO RUNNER ANSWERS '$label'. A job pinned to it then depends entirely on GitHub-hosted capacity — and when that does not materialise the run sits \`pending\` with ZERO jobs: no red, no green, the required check simply never appears (measured: python-ci-gate, runs 40898791192 / 40901844458)."
    fi
  done
  if [ "$n" -eq 0 ]; then
    # ⛔ AN EMPTY SURFACE IS NOT A GREEN ONE: when no label could be counted there is nothing to
    # have answered, so the block says it read nothing rather than "every one is covered".
    say "label-coverage: read NO \`runs-on:\` label — this makes NO claim about coverage"
  elif [ -n "$zero" ]; then
    say "label-coverage: $n label(s) read — NO online self-hosted answer for: $zero"
  else
    say "label-coverage: $n label(s) read — every one has an online self-hosted answer"
  fi
  if [ "${unreadable:-0}" -gt 0 ]; then
    say "label-coverage: ${unreadable} \`runs-on:\` key(s) use the YAML BLOCK-LIST form, which this reader does not parse — they are NOT counted above"
  fi
}

# The runner-row selector `--status` prints, and the label-coverage block counts from: name, status,
# busy flag, and the label list. It is a VARIABLE on purpose — bash 3.2 cannot parse this jq program
# written inline inside a `"$( … )"` capture (`unexpected EOF while looking for matching "`,
# measured on 3.2.57); through a variable it is plain nested quoting and parses.
RUNNER_ROWS_JQ='.runners[] | "\(.name)\t\(.status)\tbusy=\(.busy)\t\([.labels[].name]|join(","))"'

# ── status ─────────────────────────────────────────────────────────────────────
if [ "$MODE" = status ]; then
  need gh
  runners="$(gh api "repos/$REPO/actions/runners?per_page=100" --jq "$RUNNER_ROWS_JQ")" || exit 3
  if [ -n "$runners" ]; then printf '%s\n' "$runners"; fi
  label_coverage "$runners"
  for i in $(seq 1 "$COUNT"); do
    d="$(runner_dir "$i")"
    [ -f "$d/.runner" ] || continue
    agent="$(sed -n 's/.*"agentName": "\([^"]*\)".*/\1/p' "$d/.runner")"
    unit="$(unit_for "$agent")"
    state="$(systemctl is-enabled "$unit" 2>/dev/null || echo missing)/$(systemctl is-active "$unit" 2>/dev/null || echo inactive)"
    say "dir=$d unit=$unit enabled/active=$state"
  done
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
      # what `_preserve_labels` drops on re-registration). An unrecognised value is read as `off` —
      # the invariant-consistent side, never a silent keep — and says so, once per such label.
      if is_version_pinned_hosted_label "$l"; then
        case "${CI_RUNNER_OS_LABEL:-off}" in
          on|auto) pinned_kept="${pinned_kept:+$pinned_kept,}$l" ;;
          off) pinned_dropped="${pinned_dropped:+$pinned_dropped,}$l"; continue ;;
          *) pinned_dropped="${pinned_dropped:+$pinned_dropped,}$l"
             say "WARNING: CI_RUNNER_OS_LABEL='$CI_RUNNER_OS_LABEL' is not on|auto|off — read as off, so the version pin '$l' is not kept"; continue ;;
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
