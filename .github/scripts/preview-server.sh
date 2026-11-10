#!/usr/bin/env bash
# preview-server.sh — per-job preview ports and pid-scoped teardown (#10283).
#
# WHY THIS EXISTS
# ---------------
# Three `ci.yml` jobs boot a local Pages preview (`welcome-e2e`, `legal-e2e`,
# `dashboard-e2e`). They used to boot on the FIXED loopback ports 8788/8790, log
# to FIXED `/tmp/wrangler*.log` paths, record pids in FIXED `/tmp/wrangler*.pid`
# files, and tear down with machine-global `pkill workerd` /
# `pkill -f 'wrangler.*8788'`. On this repo's self-hosted pool several runner
# AGENTS share ONE host (`pi-runner-cmux-x64-5/6/7/8` all report
# `Machine name: 'cmux'`), so:
#
#   * a peer job's teardown `pkill workerd` kills THIS job's preview mid-run;
#   * a second job cannot bind the fixed port, while its readiness `curl`
#     SUCCEEDS against the FIRST job's server — silently grading a peer's
#     checkout, the #2757 shape;
#   * `kill $(cat /tmp/wrangler.pid)` can only ever address whatever pid the
#     LAST writer put there, which may be another job's server.
#
# Measured 2026-10-30 (PR #10179, `legal-e2e` job 123953321176 on
# `pi-runner-cmux-x64-8`): its previews were serving at 08:12:03Z, and its last
# spec failed at 08:12:29Z with `net::ERR_CONNECTION_REFUSED` on :8790 — 3.2 s
# after a PEER `legal-e2e` (job 123951276145, `pi-runner-cmux-x64-7`) logged its
# teardown `pkill workerd` at 08:12:25.8Z. The servers did not return wrong
# content; they were gone.
#
# THE CONTRACT
# ------------
#   * PORTS are per-run/per-job (a seeded scan over a range, verified FREE before
#     use), never a shared literal.
#   * STATE lives under the directory `dir` prints — `$RUNNER_TEMP`, which is a
#     per-JOB directory even when several runner agents share one host. It is
#     computed by THIS script and nowhere else, so the boot step's log path and
#     the teardown's pid file can never disagree.
#   * TEARDOWN signals exactly the recorded pid and its DESCENDANTS. No `pkill`,
#     no pattern match, no process name. A pid whose `/proc` start time no longer
#     matches the one recorded at boot is treated as RECYCLED and is not
#     signalled at all.
#
# Subcommands
#   alloc --name N [--base PORT] [--span N]   pick a free port, record it, print it
#   dir                                       print the per-job state directory
#   record --name N --pid PID                 register the pid this job started
#   ready --name N                            exit 0 iff OUR pid is alive, the port
#                                             ANSWERS, and that listener answers HTTP.
#                                             It does NOT prove the port's LISTENER is
#                                             ours: that needs a socket→pid map
#                                             (`ss`/`lsof`), which the runner image
#                                             does not guarantee. So `alloc`'s pre-boot
#                                             free-check and wrangler's exit on
#                                             EADDRINUSE are what keep a peer's server
#                                             from being graded.
#   alive --name N                            exit 0 iff OUR recorded process is still
#                                             RUNNING (not a zombie) AND the port it
#                                             recorded still answers HTTP. The
#                                             start-time witness `record` left is
#                                             RE-READ and reported: a match says the
#                                             identity was checked; a read that fails
#                                             (or a name with no witness) says the
#                                             IDENTITY was NOT checked and the verdict
#                                             is "alive and serving", never "the
#                                             process we started survived" — the
#                                             identity leg degrades, exit 0 does not
#                                             become a claim it did not earn. A read
#                                             that fails because the process exited is
#                                             GONE. Every other outcome gets its OWN
#                                             exit code and message — see `cmd_alive`.
#                                             This is the verb a FAILURE dump calls:
#                                             it separates "the preview died mid-run"
#                                             from "the specs were wrong", which a
#                                             bare `kill -0` cannot do.
#   stop --name N                             signal the recorded tree only
#   stop-all                                  stop every server this job recorded
#
# Exit codes: 0 ok · 2 usage · 3 no free port · 4 unusable pid
#   `alive` adds its own: 7 alive-but-not-serving · 8 pid recycled · 5 recorded then
#   stopped · 6 never recorded · 10 record was ATTEMPTED and REFUSED (the process
#   was already gone when the boot step tried to register it — `ci.yml`'s own
#   "wrangler already died") · 1 gone (died mid-run, or it exited between the two
#   probes) · 9 alive, but NO port was recorded, so SERVING was not checked ·
#   12 the pid FILE itself could not be read, so the dump took NO verdict at all
#   (never reported as GONE — it measured nothing). 0 is
#   therefore never returned when the SERVING leg was not taken; the IDENTITY leg
#   may be untaken on a serving verdict, and the message says so — see `alive`
#   above. The MESSAGE carries the same fact; the step only decides whether to
#   prefix `::error::`, so the code is the contract and the prose is for the human
#   reading the red.
set -euo pipefail

PROG="$(basename "$0")"

die() { printf '%s: %s\n' "$PROG" "$*" >&2; exit 2; }

# The per-job state directory. `PREVIEW_STATE` is set by the workflow (one place);
# the fallback keeps a bare local invocation working, and is still per-run/per-job.
state_dir() {
  local root
  if [[ -n "${PREVIEW_STATE:-}" ]]; then
    printf '%s\n' "$PREVIEW_STATE"
    return 0
  fi
  root="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
  printf '%s/tortoise-preview/%s-%s\n' \
    "${root%/}" \
    "${GITHUB_RUN_ID:-local}" \
    "$(printf '%s' "${GITHUB_JOB:-local}" | tr '/' '_')"
}

# Is anything LISTENING on this port? A connect test, not a curl: a bound
# non-HTTP socket still means the port is taken. (bash's /dev/tcp opens fd 3 in
# a subshell, which the subshell's exit closes.)
port_free() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return 1
  return 0
}

# Fields 3.. of /proc/<pid>/stat, i.e. everything after the `(comm)` field —
# parsed from the LAST ')' because a comm may itself contain spaces.
proc_rest_fields() {
  local line
  IFS= read -r line < "/proc/$1/stat" || return 1
  printf '%s\n' "${line##*') '}"
}

# The kernel's process start time (field 22 of /proc/<pid>/stat). It only changes
# when the pid is REUSED, which is what makes it the pid-identity witness we
# record at boot and re-check at teardown.
proc_starttime() {
  local rest
  rest="$(proc_rest_fields "$1")" || return 1
  # rest starts at field 3, so field 22 is the 20th token.
  awk '{print $20}' <<<"$rest"
}

# ── the same two questions on a host WITHOUT /proc ───────────────────────────
# Linux exposes /proc; macOS does not, and the arm that proves this helper's
# stop is pid-scoped runs on a developer's machine as well as in CI. A host
# capability gap must degrade the READ, never produce a wrong VERDICT (#5049):
# without this, `record` refused every pid and `stop` could not tell "already
# exited" from "cannot look", so the isolation arms RED on a Mac while the same
# logic is correct on the Linux legs. `ps` answers both questions portably —
# `state=` gives the state letter (a zombie answers kill(0) forever and is dead
# for our purposes) and `lstart=` is a start instant that changes exactly when
# the pid is reused, which is all the identity witness has to be.
have_proc() { [[ -r "/proc/$$/stat" ]]; }

pid_state() {
  if have_proc; then
    proc_rest_fields "$1" 2>/dev/null | awk '{print $1}'
  else
    ps -o state= -p "$1" 2>/dev/null | tr -d ' ' | cut -c1
  fi
}

# Running = signalable AND not a zombie.
pid_running() {
  kill -0 "$1" 2>/dev/null || return 1
  [[ "$(pid_state "$1")" != "Z" ]]
}

# Is $1 OUR recorded process? Two questions, not one (#11675 review):
#
#   (a) it is RUNNING at all — `pid_running`, because a zombie answers kill(0)
#       forever and must never be read as alive;
#   (b) it is the same PROCESS — the start-time witness `record` wrote, re-read
#       here. Pids are recycled, and comparing the pid against its creation time
#       is the portable identity rule (psutil's own: "reliable process identity
#       requires comparing the PID with the process creation time"). Linux's
#       race-free alternative — `pidfd_open` (man 2), which stays bound to the
#       process across a pid's reuse — is not available to a bash helper that
#       also runs on macOS, so the witness is the answer here.
#
# $2 is the boot-witnessed start time. An EMPTY witness means identity cannot be
# checked at all: that degrades the READ to liveness-only, never to a wrong
# VERDICT that a stranger is ours (#5049).
pid_is_ours() {
  # ONLY "ours" opens a gate. GONE / RECYCLED / UNVERIFIABLE all fail CLOSED — a
  # boot gate must not admit a process whose identity it could not check — and
  # `pid_identity` keeps them distinct so `alive` can REPORT which one it got
  # instead of collapsing them into a boolean (#11675 review, finding 1).
  pid_identity "$1" "$2" && return 0
  return 1
}

# The start time `pid_identity` last READ — "" when the read failed — so a caller
# that has to quote it in a message does not have to take the same probe twice.
PID_IDENTITY_NOW=""

# …but FOUR answers, not two, because a consumer that REPORTS a verdict has to say
# which one it got: collapsing "it matches" and "it could not be read" is exactly
# what made `alive` print a comparison it never ran (#11675 review, finding 1).
#
#   0 — OURS          running, and either the witness matches or there was none.
#   1 — GONE          not running when asked: dead, a zombie, or it exited between
#                     `alive`'s own liveness probe and this one — the window psutil
#                     documents as inherent to the pid + creation-time rule.
#   2 — RECYCLED      running, the start time READ, and it differs: a stranger.
#   3 — UNVERIFIABLE  running, but the start time could not be READ. NOT a match
#                     (the false claim this exists to prevent) and NOT a stranger
#                     (a host gap must not become a verdict — #5049).
pid_identity() {
  local pid="$1" recorded="$2" now
  PID_IDENTITY_NOW=""
  pid_running "$pid" || return 1
  [[ -n "$recorded" ]] || return 0
  now="$(pid_starttime "$pid" 2>/dev/null || true)"
  PID_IDENTITY_NOW="$now"
  if [[ -z "$now" ]]; then
    # The READ failed, and its two causes must not be reported alike: the host
    # could not answer, or the process exited in the window. Re-probe liveness —
    # an exit is a verdict, a host gap is not.
    pid_running "$pid" || return 1
    return 3
  fi
  [[ "$now" == "$recorded" ]] || return 2
  return 0
}

# The port legs: (a) something must be LISTENING, and (b) it must answer an HTTP
# success. Factored out so `ready` and `alive` cannot drift into two definitions
# of "the preview is serving" — they already shared this prose once.
serves_now() {
  local port="$1"
  [[ -n "$port" ]] || return 1
  port_free "$port" && return 1
  curl -fsS --max-time 2 "http://127.0.0.1:${port}/" >/dev/null 2>&1 || return 1
  return 0
}

pid_starttime() {
  if have_proc; then
    proc_starttime "$1"
  else
    ps -o lstart= -p "$1" 2>/dev/null
  fi
}

# Every descendant pid of $1, from one `ps` snapshot (children first is not
# guaranteed; a signal is order-insensitive for TERM/KILL purposes).
descendants_of() {
  ps -eo pid=,ppid= 2>/dev/null | awk -v root="$1" '
    { kids[$2] = kids[$2] " " $1 }
    END {
      top = 0; stack[++top] = root; seen[root] = 1
      while (top > 0) {
        cur = stack[top--]
        m = split(kids[cur], arr, " ")
        for (i = 1; i <= m; i++) {
          if (arr[i] == "" || (arr[i] in seen)) continue
          seen[arr[i]] = 1
          print arr[i]
          stack[++top] = arr[i]
        }
      }
    }'
}

cmd_alloc() {
  local name="" base=8788 span=200
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name) name="${2:-}"; shift 2 ;;
      --base) base="${2:-}"; shift 2 ;;
      --span) span="${2:-}"; shift 2 ;;
      *) die "alloc: unknown argument '$1'" ;;
    esac
  done
  [[ -n "$name" ]] || die "alloc: --name is required"
  [[ "$base" =~ ^[0-9]+$ && "$span" =~ ^[0-9]+$ ]] || die "alloc: --base/--span must be numeric"

  # A per-run/per-job/per-server offset, so two jobs on one host do not scan from
  # the same candidate. `cksum` keeps this POSIX (no openssl dependency).
  local seed offset i cand dir
  seed="$(printf '%s:%s:%s' "${GITHUB_RUN_ID:-0}" "${GITHUB_JOB:-local}" "$name" | cksum | cut -d' ' -f1)"
  offset=$(( seed % span ))
  dir="$(state_dir)"
  mkdir -p "$dir"
  for (( i = 0; i < span; i++ )); do
    cand=$(( base + ((offset + i) % span) ))
    if port_free "$cand"; then
      printf '%s\n' "$cand" > "$dir/${name}.port"
      printf '%s\n' "$cand"
      return 0
    fi
  done
  printf '::error::%s: no free port in %s..%s\n' "$PROG" "$base" "$((base + span - 1))" >&2
  return 3
}

cmd_record() {
  local name="" pid=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name) name="${2:-}"; shift 2 ;;
      --pid) pid="${2:-}"; shift 2 ;;
      *) die "record: unknown argument '$1'" ;;
    esac
  done
  [[ -n "$name" ]] || die "record: --name is required"
  [[ "$pid" =~ ^[0-9]+$ ]] || die "record: --pid must be numeric"

  local dir start
  dir="$(state_dir)"
  mkdir -p "$dir"
  # ⛔ THE REFUSAL WITNESS, written BEFORE either refusal can fire. Exit 4 on
  # either path means the process this job spawned was ALREADY GONE, which
  # `ci.yml` spells out ("`|| true`: exit 4 means wrangler already died, so there
  # is nothing to record") — the single most common "the preview DIED" case. The
  # boot witness below is unreachable on both, so without this file `alive`
  # reported that state as "NEVER RECORDED … the boot step never reached record",
  # i.e. it named the OPPOSITE mechanism and pointed the reader at the boot wiring
  # instead of at a process that spawned and exited (#11675 review, finding 1: the
  # same defect class as the bare `kill -0` this verb replaced).
  #
  # It records the pid AND which predicate refused, because the two refusals send
  # the reader to different places: "not running" means the spawn died, "cannot
  # read the start time" means `/proc` lost it under us. Never removed — not by
  # `stop`, not by `stop-all` — for the same reason `.booted` is not.
  pid_running "$pid" || {
    printf 'refused %s not-running\n' "$pid" > "$dir/${name}.refused"
    printf '%s: pid %s is not running — nothing recorded\n' "$PROG" "$pid" >&2
    return 4
  }

  start="$(pid_starttime "$pid")" || {
    printf 'refused %s no-start-time\n' "$pid" > "$dir/${name}.refused"
    printf '%s: cannot read the start time of pid %s\n' "$PROG" "$pid" >&2
    return 4
  }
  printf '%s\n' "$start" > "$dir/${name}.start"
  printf '%s\n' "$pid" > "$dir/${name}.pid"
  # The BOOT WITNESS, and it is deliberately never removed — not by `stop`, not
  # by `stop-all`. `stop` deletes `.pid`/`.start`/`.port` on EVERY branch, and
  # the boot step's own retry loop calls `stop` between attempts, so a missing
  # pid file has two causes that must not be reported alike: a preview that WAS
  # recorded and was then stopped, and a boot that never reached `record`. Only
  # this file separates them (#11675 review, third false-verdict path).
  printf '%s %s\n' "$pid" "$start" > "$dir/${name}.booted"
  # A successful `record` SUPERSEDES an earlier refusal for this name (the boot
  # step's retry loop can refuse on attempt 1 and succeed on attempt 2). Without
  # this, the stale refusal would be read by `alive` if the pid file later went
  # missing — reporting a preview that DID boot as one that never could.
  rm -f "$dir/${name}.refused"
}

# `--name N` (the workflow form) and a bare positional (debugging form).
name_flag() {
  local verb="$1"; shift
  local name=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name) name="${2:-}"; shift 2 || shift ;;
      *) name="$1"; shift ;;
    esac
  done
  [[ -n "$name" ]] || die "$verb: --name is required"
  printf '%s\n' "$name"
}

cmd_ready() {
  local name
  name="$(name_flag ready "$@")"
  local dir pid port
  dir="$(state_dir)"
  [[ -f "$dir/${name}.pid" && -f "$dir/${name}.port" ]] || return 1
  pid="$(cat "$dir/${name}.pid")"
  port="$(cat "$dir/${name}.port")"
  # (a) The pid we recorded must still be ALIVE AND still be OURS. Three facts are
  # checked here and NOTHING relates them to each other: (a) liveness+identity,
  # (b) something ANSWERS on the port, (c) that answer is an HTTP success.
  # Mapping the port's listener to a pid needs `ss`/`lsof`, which the runner image
  # does not guarantee, so a live-but-unbound pid beside a PEER's listener still
  # passes. What narrows that window is `alloc`'s pre-boot free-check and
  # wrangler's own exit on EADDRINUSE, not this probe (#2757).
  #
  # (a) is `pid_is_ours`, not a bare `kill -0`: a zombie answers kill(0) forever,
  # and a RECYCLED pid would let a stranger satisfy this leg while our server is
  # gone — then (c) could pass on the stranger's answer, which is the #2757 shape
  # one step over (#11675 review). An identity that could not be READ is refused
  # too: the boot loop retries, and a gate that cannot verify must not open.
  pid_is_ours "$pid" "$(cat "$dir/${name}.start" 2>/dev/null || true)" || return 1
  # (b)+(c) …something must be LISTENING there, and it must be SERVING, not merely
  # bound: a wrangler whose listener is up while its bundle/workerd is still
  # starting answers a connect but never a 200, and the suites behind this gate
  # require HTTP 200 (`tests/e2e/test_session_login_flow.py` exits the run when it
  # is not). `-f` fails on HTTP >= 400; `--max-time` keeps a listener that accepts
  # but never replies from stalling the 240-iteration loop. This is the pre-fix
  # `curl -fsS` requirement, kept beside the properties the #10283 fix added.
  serves_now "$port" || return 1
  return 0
}

# ── alive: "did OUR preview survive the run?" ────────────────────────────────
#
# The ONE question a failure dump has to answer before a log can be read for a
# cause, and the reason this verb exists: both `ci.yml` preview-diagnostic steps
# decided it with a bare `kill -0 "$pid"`, which is wrong in THREE ways measured
# by the review of PR #11675.
#
#   * A ZOMBIE answers kill(0) forever, so an unreaped wrangler read as ALIVE —
#     and the step then asserted the CAUSE of the red ("the specs' or the
#     content's, not a dead server") from a process that had already exited.
#     `pid_running` is the predicate `record`/`stop` already use.
#   * A RECYCLED pid is a different process, so the same bare probe read a
#     stranger as ours. The identity witness is the start time `record` wrote
#     and `stop`'s PID-REUSE GUARD already re-checks; see `pid_is_ours`.
#   * "No pid file" was reported as "it never started" — but `stop` deletes the
#     pid file on every branch and the boot retry loop calls it, so the truth was
#     the OPPOSITE of the message in the common double-boot-failure case. The
#     `.booted` witness `record` writes separates the two.
#   * "No pid file" ALSO folded in `record` ATTEMPTED AND REFUSED — the exit-4
#     paths (the process was already gone, or its start time was unreadable)
#     return before `.booted` can be written. That is `ci.yml`'s "wrangler already
#     died", and it was reported as "the boot step never reached record", which
#     is the OPPOSITE mechanism. The `.refused` witness separates it
#     (#11675 review, finding 1).
#
# Exit codes (see the header table, which this list must not drift from):
# 0 alive and serving · 7 alive but not serving · 8 pid recycled · 5 recorded
# then stopped · 6 never recorded · 10 record attempted and REFUSED · 1 gone ·
# 9 alive but no port recorded, so SERVING was not checked · 12 the pid FILE
# itself could not be read, so NO verdict was taken (never reported as gone).
cmd_alive() {
  local name
  name="$(name_flag alive "$@")"
  local dir pfile pid port recorded
  dir="$(state_dir)"
  pfile="$dir/${name}.pid"
  port="$(cat "$dir/${name}.port" 2>/dev/null || true)"
  local at_port="" stranger=""
  [[ -n "$port" ]] && at_port=" on :${port}"
  # …and the RECYCLED sentence cannot splice an empty `at_port` into the middle of
  # a clause ("anything answering is a stranger's answer"), so it carries its own
  # port-aware form — the same guard the no-port branch below already applies
  # (#11675 review, finding 2).
  if [[ -n "$port" ]]; then
    stranger="anything answering on :${port} is a stranger's answer, never this preview's"
  else
    stranger="anything answering at all is a stranger's answer, never this preview's, and no port was recorded for this name so WHERE it would answer was NOT checked"
  fi

  # NAMES are for humans; the exit code is what the step branches on, and every
  # state below gets its own so a reader (and a test) can tell them apart.
  if [[ ! -f "$pfile" ]]; then
    # ⛔ THE LAST ATTEMPT DECIDES, not the first artefact this loop happens to
    # find. The boot step's retry loop can SUCCEED on attempt 1 (`record` writes
    # `.booted`), be stopped by the timeout branch (`stop` deletes the pid file
    # and keeps `.booted`), and then be REFUSED on attempt 2 (`.refused`). Both
    # witnesses are on disk, and checking `.booted` first reported that state as
    # "RECORDED, then STOPPED" — discarding the one datum that says the LAST
    # spawn STARTED AND DIED (measured: `alive` returned 5 for that sequence).
    # The later of the two witnesses wins; a tie goes to the refusal, because a
    # refusal is the more specific statement about the attempt that explains the
    # red.
    local earlier_note=""
    if [[ -f "$dir/${name}.refused" ]] && {
      [[ ! -f "$dir/${name}.booted" ]] ||
      [[ ! "$dir/${name}.refused" -ot "$dir/${name}.booted" ]]; }; then
      if [[ -f "$dir/${name}.booted" ]]; then
        earlier_note=" (an EARLIER attempt DID record and was then stopped, so this refusal describes the LAST attempt — the one whose failure explains the red)"
      fi
      # The boot step DID call `record`, and `record` RAN — it just refused, which
      # means the process this job spawned was already gone (or `/proc` lost its
      # start time) at the moment it was registered. That is a preview that
      # STARTED AND DIED before it could be recorded, NOT a boot wiring problem,
      # and conflating the two is the defect this branch removes.
      local why
      why="$(cat "$dir/${name}.refused" 2>/dev/null || true)"
      printf '%s\n' "${PROG}: ${name}: RECORD REFUSED — the boot step DID reach record and record REFUSED (${why#refused }), because the process it had just spawned was already gone (or its start time could not be read) when it was registered. This is a preview that STARTED AND DIED, not one that never started: read the boot step's own log for why the process exited. A boot that never reached record reports itself as NEVER RECORDED, not as this.${earlier_note}"
      return 10
    fi
    if [[ -f "$dir/${name}.booted" ]]; then
      printf '%s\n' "${PROG}: ${name}: RECORDED, then STOPPED — this job DID start a preview and its pid file is gone, which is what stop does on every branch (the boot step's retry loop calls it between attempts, and so may a spec). This does NOT mean the preview never started: read the boot step's own log for why it was stopped."
      return 5
    fi
    printf '%s\n' "${PROG}: ${name}: NEVER RECORDED — no pid was ever recorded for this name in this job's state dir, so the boot step never reached record: read its log. A preview that started and was then stopped reports itself as RECORDED, then STOPPED, not as this."
    return 6
  fi

  # ⛔ Under `set -e` an unguarded read here exited 1 with EMPTY stdout — a blank
  # `::error::` in the step, wearing the code the header reserves for GONE, from a
  # dump that measured nothing (measured: `chmod 000` on the pid file). Every
  # other read in this function already carries `|| true`; this one must not be
  # the exception, and a state this probe could not read is neither a survived
  # verdict nor a gone one.
  pid="$(cat "$pfile" 2>/dev/null || true)"
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "${PROG}: ${name}: PID FILE UNREADABLE — ${pfile} is present but no pid could be read from it (unreadable, empty, or not numeric), so this dump took NO verdict: the process may be alive or dead. Read the log and the boot step; do NOT read this as GONE."
    return 12
  fi
  recorded="$(cat "$dir/${name}.start" 2>/dev/null || true)"

  # Zombies answer kill(0) forever: liveness is `pid_running`, never bare kill -0.
  if ! pid_running "$pid"; then
    printf '%s\n' "${PROG}: ${name}: pid ${pid}${at_port} is GONE — the process this job recorded is no longer running (it exited, or it is a zombie that has not been reaped), so this preview DIED MID-RUN. Its log is the cause, and every ECONNREFUSED in the specs above is a SYMPTOM of it."
    return 1
  fi

  # The PID-REUSE GUARD `stop` already applies, applied to the READ — through the
  # SAME predicate `record` and `ready` use, so one question cannot have two
  # answers. A pid whose start time changed is a different process, and the preview
  # we started is gone.
  #
  # ⛔ A VERDICT MAY NOT CLAIM A FACT THE PROBE NEVER TOOK. Each of the predicate's
  # four answers gets its OWN sentence: "still matches the boot witness" is printed
  # only when the comparison was actually TAKEN, and a start time that could not be
  # re-read says so instead of asserting a match nobody performed — the same class
  # of defect as the bare `kill -0` this verb replaced (#11675 review, finding 1).
  local identity ident=0
  if [[ -z "$recorded" ]]; then
    identity="no start-time witness was recorded, so its IDENTITY was NOT checked"
  else
    pid_identity "$pid" "$recorded" || ident=$?
    case "$ident" in
      0) identity="its start time still matches the boot witness" ;;
      1)
        printf '%s\n' "${PROG}: ${name}: pid ${pid}${at_port} is GONE — the process this job recorded is no longer running: it exited between this check and its start-time re-read, or it is a zombie that has not been reaped. This preview DIED MID-RUN. Its log is the cause, and every ECONNREFUSED in the specs above is a SYMPTOM of it."
        return 1
        ;;
      2)
        printf '%s\n' "${PROG}: ${name}: pid ${pid} was RECYCLED — it is running, but its start time (${PID_IDENTITY_NOW}) no longer matches the one witnessed at boot (${recorded}), so it is a DIFFERENT process and the preview this job started is GONE. It DIED MID-RUN; its log is the cause, and ${stranger}."
        return 8
        ;;
      *) identity="its start time could NOT be read, so its IDENTITY was NOT checked" ;;
    esac
  fi

  if [[ -z "$port" ]]; then
    printf '%s\n' "${PROG}: ${name}: pid ${pid} is ALIVE — our recorded process: not a zombie, and ${identity} — but no port was recorded for this name, so whether it was still SERVING was NOT checked. This is NOT an alive-and-serving verdict; the log is the next read."
    return 9
  fi

  if serves_now "$port"; then
    if [[ -n "$recorded" && "$ident" -eq 0 ]]; then
      printf '%s\n' "${PROG}: ${name}: pid ${pid} on :${port} is ALIVE (our recorded process: not a zombie, and ${identity}) and :${port} answers HTTP — this preview SURVIVED the run, so the red is the specs' or the content's, not a dead server. This does NOT prove the listener on the port is ours: alloc's pre-boot free-check and wrangler's own exit on EADDRINUSE are what exclude a peer's server, not this probe."
      return 0
    fi
    # The identity leg was NOT taken (no witness, or a witness that could not be
    # re-read), so the verdict may not borrow it: it reports what the probe DID
    # take — the recorded pid runs, the port answers — and says the other leg is
    # unchecked. Same rule as the sentence above (`#11675` review, finding 3):
    # returning a non-zero here would mark a live preview dead on a box where
    # `/proc` is unreadable, a false red; borrowing the identity is a false green.
    printf '%s\n' "${PROG}: ${name}: pid ${pid} on :${port} is ALIVE (not a zombie, and ${identity}) and :${port} answers HTTP. Read this as ALIVE AND SERVING, not as 'the process we started survived': ${identity}, so that leg is NOT evidence either way. Whether the red is the specs' or a dead server is therefore undecided by this probe."
    return 0
  fi
  if [[ -n "$recorded" && "$ident" -eq 0 ]]; then
    printf '%s\n' "${PROG}: ${name}: pid ${pid} on :${port} is ALIVE (our recorded process: not a zombie, and ${identity}) but :${port} does not answer an HTTP success — nothing is listening there, or the answer is a non-2xx. The PROCESS survived and the SERVER did not serve; the log is where the cause is."
    return 7
  fi
  printf '%s\n' "${PROG}: ${name}: pid ${pid} on :${port} is ALIVE (not a zombie, and ${identity}) but :${port} does not answer an HTTP success — nothing is listening there, or the answer is a non-2xx. The recorded PID is running and nothing served on its port; ${identity}, so 'it is still OUR process' is NOT established by this probe. The log is where the cause is."
  return 7
}

cmd_stop() {
  local name
  name="$(name_flag stop "$@")"
  local dir pfile pid recorded now
  dir="$(state_dir)"
  pfile="$dir/${name}.pid"
  if [[ ! -f "$pfile" ]]; then
    printf '%s: nothing recorded for %s\n' "$PROG" "$name"
    return 0
  fi
  pid="$(cat "$pfile")"
  recorded="$(cat "$dir/${name}.start" 2>/dev/null || true)"

  if ! pid_running "$pid"; then
    printf '%s: %s (pid %s) already exited\n' "$PROG" "$name" "$pid"
    rm -f "$pfile" "$dir/${name}.start" "$dir/${name}.port"
    return 0
  fi

  # PID-REUSE GUARD: a recycled pid is not our server. Refusing is fail-safe —
  # leaving a stray preview alive costs a runner reset; signalling a stranger is
  # the class of bug this file exists to remove.
  if [[ -n "$recorded" ]]; then
    now="$(pid_starttime "$pid" || true)"
    if [[ -n "$now" && "$now" != "$recorded" ]]; then
      printf '%s: pid %s was recycled (start %s != %s) — not signalling it\n' \
        "$PROG" "$pid" "$now" "$recorded"
      rm -f "$pfile" "$dir/${name}.start" "$dir/${name}.port"
      return 0
    fi
  fi

  local victims=() p i count
  # A read loop, not `mapfile`: bash 3.2 (macOS `/bin/bash`) has no `mapfile`,
  # and this helper's isolation arms run on a developer's machine as well as in
  # CI (#5049: a host gap must not become a verdict — the failure it caused was
  # the arm erroring out on a host that cannot even parse the script).
  while IFS= read -r p; do
    [[ -n "$p" ]] && victims+=("$p")
  done < <(descendants_of "$pid"; printf '%s\n' "$pid")
  count=${#victims[@]}
  # `${arr[@]+…}` keeps this valid on bash 3.2 under `set -u` when the array is
  # empty (a bare "${arr[@]}" is an unbound-variable ERROR there, not zero words).
  for p in "${victims[@]+"${victims[@]}"}"; do kill -TERM "$p" 2>/dev/null || true; done
  for (( i = 0; i < 100; i++ )); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.1
  done
  if pid_running "$pid"; then
    # Re-walk: a supervisor may have spawned replacements while we waited.
    local retry=()
    while IFS= read -r p; do
      [[ -n "$p" ]] && retry+=("$p")
    done < <(descendants_of "$pid"; printf '%s\n' "$pid")
    count=$(( count + ${#retry[@]} ))
    for p in "${retry[@]+"${retry[@]}"}"; do kill -KILL "$p" 2>/dev/null || true; done
  fi
  rm -f "$pfile" "$dir/${name}.start" "$dir/${name}.port"
  printf '%s: stopped %s (pid %s and %s descendant(s))\n' \
    "$PROG" "$name" "$pid" "$(( count - 1 ))"
}

cmd_stop_all() {
  local dir f name rc=0
  dir="$(state_dir)"
  [[ -d "$dir" ]] || { printf '%s: no state directory at %s\n' "$PROG" "$dir"; return 0; }
  for f in "$dir"/*.pid; do
    [[ -e "$f" ]] || continue
    name="$(basename "$f" .pid)"
    cmd_stop "$name" || rc=$?
  done
  return "$rc"
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    alloc) cmd_alloc "$@" ;;
    dir) state_dir ;;
    record) cmd_record "$@" ;;
    ready) cmd_ready "$@" ;;
    alive) cmd_alive "$@" ;;
    stop) cmd_stop "$@" ;;
    stop-all) cmd_stop_all ;;
    *) die "usage: $PROG {alloc|dir|record|ready|alive|stop|stop-all} ..." ;;
  esac
}

main "$@"
