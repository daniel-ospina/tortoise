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
#   ⚠️ And the reverse holds too: a runner that carries `ubuntu-latest` unconditionally makes
#   the gate's `current_mode` read `local` forever (it counts runners carrying that label),
#   so the gate could never return CI to GitHub-hosted and would instead `fallback_off` —
#   which stamps the ARM64 pool label onto these x86 runners. That is why the label is
#   gate-following rather than permanent; see `--repair-labels`.
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
#   tools/install-ci-runner-x64.sh --repair-labels --count 2  # re-sync labels to the gate's state
#   tools/install-ci-runner-x64.sh --status                   # remote view + local units
#   tools/install-ci-runner-x64.sh --uninstall --count 2      # deregister + remove
#   --fallback auto|on|off   (default auto = follow the ARM64 pool's current state)
#
# REBOOT: the runner is a systemd service (`svc.sh install`), enabled at install time, so it
#   comes back on boot with no human step. See docs/ops/ci-runner-x64.md.
#
# EXIT: 0 ok · 1 usage · 2 guard refused (headroom/arch/undecidable fallback) · 3 install failed
set -uo pipefail

REPO="${CI_RUNNER_REPO:-daniel-ospina/tortoise}"
BASE="${CI_RUNNER_BASE:-/opt/gh-runner-x64}"
NAME_PREFIX="${CI_RUNNER_NAME_PREFIX:-pi-runner-x64}"
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
CORES_PER_RUNNER=2
CORES_RESERVED_FOR_AGENT_LANES=2
DISK_PER_RUNNER_GB=8       # checkout (~0.2G) + uv venv + docker images, measured per lane
MEM_PER_RUNNER_GB=2
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
    -h|--help) sed -n '1,44p' "$0"; exit 0 ;;
    *) echo "usage: $0 [--count N] [--dry-run] [--uninstall] [--status] [--repair-labels] [--fallback auto|on|off]" >&2; exit 1 ;;
  esac
done
# ⛔ NOT `${COUNT:-2}` AT ASSIGNMENT TIME: a literal `COUNT=0` is "set", so a `:-` default would
# never fire and `seq 1 0` would make `--status` / `--uninstall` a silent no-op that reports
# success (review finding 3). An empty COUNT is the only "unset" here.
DEFAULT_COUNT=2

say()  { printf '%s\n' "$*" >&2; }
die()  { say "REFUSED: $1"; exit "${2:-2}"; }
need() { command -v "$1" >/dev/null 2>&1 || die "'$1' is not on PATH (a runner without it cannot register, and a job without it exits 2 — #9018)"; }

runner_dir() { # $1 = instance number: instance 1 keeps the base dir, the rest get -<n>
  if [ "$1" = 1 ]; then printf '%s' "$BASE"; else printf '%s-%s' "$BASE" "$1"; fi
}
unit_for() { printf 'actions.runner.%s.%s.service' "$(printf '%s' "$REPO" | tr '/' '-')" "$1"; }

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
      # decision, so it is skipped and the plan says the fallback label is resolved at install.
      if [ "$DRY" = 1 ]; then
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

# ── status ─────────────────────────────────────────────────────────────────────
if [ "$MODE" = status ]; then
  need gh
  gh api "repos/$REPO/actions/runners?per_page=100" \
    --jq '.runners[] | "\(.name)\t\(.status)\tbusy=\(.busy)\t\([.labels[].name]|join(","))"' \
    || exit 3
  for i in $(seq 1 "${COUNT:-$DEFAULT_COUNT}"); do
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
# This is the documented repair when the pre-#2327 gate has relabelled these runners: it puts
# back the arch marker and syncs the fallback label to the gate's state, without re-registering.
if [ "$MODE" = repair ]; then
  need gh
  resolve_labels
  rc=0
  for i in $(seq 1 "${COUNT:-$DEFAULT_COUNT}"); do
    d="$(runner_dir "$i")"
    [ -f "$d/.runner" ] || { say "skip: $d is not configured"; continue; }
    agent="$(sed -n 's/.*"agentName": "\([^"]*\)".*/\1/p' "$d/.runner")"
    id="$(gh api --paginate "repos/$REPO/actions/runners" --jq ".runners[]|select(.name==\"$agent\")|.id" | head -1)"
    case "$id" in ''|*[!0-9]*) say "WARNING: $agent not found in the repo's runners"; rc=1; continue ;; esac
    body="$(printf '{"labels":["%s"]}' "$(printf '%s' "$LABELS" | sed 's/,/","/g')")"
    if [ "$DRY" = 1 ]; then say "dry-run: would PUT $body to runner $id ($agent)"; continue; fi
    if printf '%s' "$body" | gh api -X PUT "repos/$REPO/actions/runners/$id/labels" --input - >/dev/null 2>&1; then
      say "relabelled $agent → $LABELS"
    else
      say "WARNING: relabel FAILED for $agent (id $id)"; rc=1
    fi
  done
  exit "$rc"
fi

# ── guards: measured headroom, in the harness's own order (forks, disk, load) ──
arch="$(uname -m)"
[ "$arch" = "x86_64" ] || die "this installer is for x86_64 hosts; '$arch' would register a DIFFERENT arch label than it claims"

if [ "$MODE" = install ]; then
  case "$COUNT" in ''|*[!0-9]*) die "--count must be a positive integer (got '$COUNT')" 1 ;; esac
  [ "$COUNT" -ge 1 ] || die "--count must be >= 1 (got $COUNT)" 1

  # FORK HEADROOM FIRST — the failure the harness has hit is fork exhaustion, not CPU.
  procs=$(ps -e --no-headers 2>/dev/null | wc -l)
  fork_cap=$(ulimit -u 2>/dev/null || echo unknown)
  threads_max=$(cat /proc/sys/kernel/threads-max 2>/dev/null || echo unknown)
  case "$fork_cap" in
    ''|*[!0-9]*) die "cannot read the fork cap (ulimit -u='$fork_cap') — refusing to size against an unread bound" ;;
  esac
  need_forks=$(( COUNT * 400 + procs ))          # a pytest job + its docker children ≈ 400 procs
  [ "$need_forks" -le "$fork_cap" ] || die "fork headroom: $COUNT runners need ~$need_forks processes against ulimit -u=$fork_cap (now $procs)"
  say "headroom: forks ok ($procs now, ~$need_forks needed, cap $fork_cap, threads-max $threads_max)"

  # DISK NEXT — a full disk fails a job as a red, not as a queue delay.
  disk_free_gb=$(df -Pk "$(dirname "$BASE")" 2>/dev/null | awk 'NR==2{printf "%d", $4/1024/1024}')
  case "$disk_free_gb" in ''|*[!0-9]*) die "cannot read free disk space for $(dirname "$BASE")" ;; esac
  disk_need_gb=$(( COUNT * DISK_PER_RUNNER_GB + 20 ))   # +20G keeps the agent lanes' worktrees safe
  [ "$disk_free_gb" -ge "$disk_need_gb" ] || die "disk headroom: ${disk_free_gb}G free against ${disk_need_gb}G needed for $COUNT runners (+20G reserve)"
  say "headroom: disk ok (${disk_free_gb}G free, ${disk_need_gb}G needed)"

  # LOAD LAST — and it is a CAPACITY bound, not a live-load gate: the agent lanes are bursty,
  # so a momentary load reading must never be what decides whether CI gets capacity.
  ncpu=$(nproc)
  cpu_need=$(( COUNT * CORES_PER_RUNNER + CORES_RESERVED_FOR_AGENT_LANES ))
  [ "$cpu_need" -le "$ncpu" ] || die "cpu headroom: $COUNT runners need ${COUNT}x${CORES_PER_RUNNER} cores + ${CORES_RESERVED_FOR_AGENT_LANES} reserved for agent lanes = $cpu_need, this box has $ncpu (measured ~1.0-1.5 cores per concurrent job)"
  mem_free_gb=$(awk '/MemAvailable/{printf "%d", $2/1024/1024}' /proc/meminfo)
  mem_need_gb=$(( COUNT * MEM_PER_RUNNER_GB + 4 ))
  [ "${mem_free_gb:-0}" -ge "$mem_need_gb" ] || die "memory headroom: ${mem_free_gb}G available against ${mem_need_gb}G needed for $COUNT runners (+4G reserve)"
  say "headroom: cpu ok (${COUNT}x${CORES_PER_RUNNER} + ${CORES_RESERVED_FOR_AGENT_LANES} reserved = ${cpu_need} of ${ncpu} cores; ${mem_free_gb}G mem free, ${mem_need_gb}G needed)"
fi

# ── uninstall ─────────────────────────────────────────────────────────────────
if [ "$MODE" = uninstall ]; then
  need gh
  for i in $(seq 1 "${COUNT:-$DEFAULT_COUNT}"); do
    d="$(runner_dir "$i")"
    [ -d "$d" ] || { say "skip: $d does not exist"; continue; }
    if [ "$DRY" = 1 ]; then say "dry-run: would deregister $(basename "$d") and remove its unit"; continue; fi
    ( cd "$d" && [ -f ./svc.sh ] && sudo ./svc.sh stop >/dev/null 2>&1; sudo ./svc.sh uninstall >/dev/null 2>&1 )
    tok="$(gh api -X POST "repos/$REPO/actions/runners/remove-token" --jq .token)" || die "could not mint a removal token"
    ( cd "$d" && ./config.sh remove --token "$tok" --unattended >/dev/null 2>&1 ) \
      && say "deregistered $(basename "$d")" || say "WARNING: $(basename "$d") may still be registered — check --status"
    sudo rm -rf "$d"
  done
  exit 0
fi

# ── labels question: resolve and print, with NO guard, NO token, NO network ────
# This is the seam the invariant is tested through. It runs the SAME `resolve_labels` the install
# path runs (`:271` feeds `$LABELS` to config.sh), so a wrong label here is a wrong label there.
# It deliberately sits after every guard: asking which labels a runner would get must not depend
# on this box's memory headroom, or the invariant silently goes untested on a loaded host.
if [ "$MODE" = labels ]; then
  DRY=1   # auto resolves to "decide at install" rather than reading the pool
  resolve_labels
  printf '%s\n' "$LABELS"
  exit 0
fi

tarball="actions-runner-linux-x64-$RUNNER_VERSION.tar.gz"
url="https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/$tarball"
actor="${GITHUB_ACTOR:-cmux}"

resolve_labels
for t in $JOB_TOOLS $RUNNER_TOOLS; do
  command -v "$t" >/dev/null 2>&1 || say "WARNING: '$t' is not on PATH — jobs needing it will exit 2 (#9018)"
done

for i in $(seq 1 "$COUNT"); do
  d="$(runner_dir "$i")"; name="$NAME_PREFIX-$i"
  say "── runner $i: name=$name dir=$d labels=$LABELS"
  if [ "$DRY" = 1 ]; then say "dry-run: would download $url, verify sha256, then run config.sh --labels $LABELS, then svc.sh install+start"; continue; fi
  need gh; need curl; need tar; need sha256sum

  if [ ! -f "$d/.runner" ]; then
    sudo mkdir -p "$d" && sudo chown "$(id -un):$(id -gn)" "$d" || die "cannot create $d"
    say "downloading $url"
    curl -fsSL --max-time 300 -o "$d/$tarball" "$url" || die "download failed"
    got="$(sha256sum "$d/$tarball" | cut -d' ' -f1)"
    [ "$got" = "$SHA256_LINUX_X64" ] || { rm -f "$d/$tarball"; die "tarball sha256 mismatch: got $got, expected $SHA256_LINUX_X64 — refusing to execute it"; }
    say "sha256 verified ($got)"
    tar xzf "$d/$tarball" -C "$d" && rm -f "$d/$tarball" || die "extract failed in $d"
    tok="$(gh api -X POST "repos/$REPO/actions/runners/registration-token" --jq .token)" || die "could not mint a registration token"
    # ⛔ ONE ARGV, BUILT ONCE. The plan above prints the same string the exec below hands
    # config.sh, so a wrong label cannot pass the plan and reach registration (review finding 4).
    set -- --unattended --url "https://github.com/$REPO" --token "$tok" \
           --name "$name" --labels "$LABELS" --work _work --replace
    ( cd "$d" && ./config.sh "$@" ) || die "config.sh failed for $name"
  else
    say "already configured ($d/.runner present) — reusing (use --repair-labels to re-sync labels)"
  fi

  # systemd (system mode): this is what makes it survive a reboot. `svc.sh install <user>`
  # writes /etc/systemd/system/<unit>.service and enables it.
  if [ ! -f "/etc/systemd/system/$(unit_for "$name")" ]; then
    ( cd "$d" && sudo ./svc.sh install "$actor" ) || die "svc.sh install failed for $name"
  fi
  ( cd "$d" && sudo ./svc.sh start ) || die "svc.sh start failed for $name"
  say "started $name"
done

say ""
say "verify:"
say "  gh api repos/$REPO/actions/runners --jq '.runners[]|select(.name|startswith(\"$NAME_PREFIX\"))|{name,status,busy,labels:[.labels[].name]}'"
say "  tools/install-ci-runner-x64.sh --status"
exit 0
