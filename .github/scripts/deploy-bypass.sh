#!/usr/bin/env bash
# ══ deploy-bypass.sh — ONE renderer for the four bypassable deploy gates (#4759)
#
# WHY THIS EXISTS. A bypassed gate and a passed gate used to be
# indistinguishable to a reader of the run summary: the only trace was a
# `::warning::` inside one step's log — and the four gates did not even render
# that trace the same way (two had a dedicated "Warn when … bypassed" step, two
# emitted an inline `echo … >&2` from inside a larger step). Nothing anywhere
# checked how long a `SKIP_*` variable had been left set either, so the incident
# window closed by prose alone — which is how a correct-during-incident bypass
# stays wrong afterwards (#4605).
#
# Subcommands:
#   report  — a bypass FIRED this run. Emits the `::warning::` (auditability,
#             unchanged in kind) AND appends a uniform block to
#             $GITHUB_STEP_SUMMARY naming the gate, the lane that fired, the
#             recorded window start and the window — so the skip is visible
#             without opening a step log.
#   audit   — end-of-job. States EVERY bypassable gate in that job as bypassed
#             or not, so the ABSENCE of a bypass block is not itself ambiguous:
#             a green run says which gates ran.
#   expiry  — the scheduled machine check. Every `SKIP_*` lane left set is named
#             with its age; one left set past the window — or set with no
#             recorded start date, which cannot be checked at all — is a
#             violation → exit 1. It runs in a SEPARATE workflow
#             (skip-bypass-expiry.yml) and NEVER blocks a deploy: blocking would
#             strand the very incident-fix deploy the bypass exists for. The
#             daily failure is the reminder, and every deploy's `report` block
#             repeats the same age.
#
# ⛔ THIS SCRIPT NEVER DECIDES A GATE. It does not translate any gate's exit
#    code and is not on any gate's exit path — do not "integrate" it into
#    check-fly-machines-guard.py, check-fly-secret-drift.py or
#    deploy-health-gate.sh. It only OBSERVES that a bypass happened. But `report`
#    IS invoked inside deploy steps (as the whole `run:` body of the pack-smoke
#    and DB-health steps, and under `set -e` inside both Fly-guard steps), so a
#    `report` failure DOES fail its own step and can fail the job. That is
#    deliberate and fail-closed: a reporter that cannot certify a bypass must
#    not be indistinguishable from one that did. Its non-zero exits are
#    `report`'s no-lane-set wiring bug (1) and `die()`'s bad-invocation paths —
#    unknown key/subcommand/argument, malformed `--state`, non-integer
#    `--window-days`, non-date `--today` (64). `expiry`'s violation (1) is the
#    only other one, and it runs in a SEPARATE scheduled workflow, so it never
#    blocks a deploy.
#
# ⛔ exit 2 IS NOT OURS TO TOUCH. The could-not-determine class stays
#    unbypassable (#1896/#4126); a bypass translates exit 1 only, and that
#    decision lives in deploy-hosted.yml, never here.
#
# WHERE THE WINDOW COMES FROM. `gh variable list --json updatedAt` cannot be the
# source: the endpoint needs the fine-grained "Variables" permission, which the
# workflow `GITHUB_TOKEN` does not carry, and the `vars` context exposes a
# variable's VALUE but not its `updatedAt`. So the window start is an explicit
# companion variable, `SKIP_<GATE>_SET_AT` (`YYYY-MM-DD`, UTC), read through the
# ordinary `vars` context — no extra token scope, works on the push lane where
# `inputs` is null. Forgetting it is NOT silent: a `SKIP_*` set to `true` with no
# usable `_SET_AT` is an `expiry` violation (and an OVERDUE/NOT RECORDED callout
# in every deploy's report block), so the failure direction is a loud red, never
# a quiet stale bypass. See docs/infra-runbook.md §8.2.
set -uo pipefail

# The canonical gate table — the bypassable deploy gates. `report` callers
# name a gate by its repo variable and the label/dispatch-input are looked up
# here, so a rename cannot half-land; `expiry` iterates the same list; `audit`
# reads the KIND to know what a set lane means.
#
# THIS TABLE IS THE DECLARATION. The structural guard in
# tests/test_deploy_workflow.py derives every workflow surface from it — the
# `workflow_dispatch` inputs, each report `--key`, each audit `--state`, the
# expiry workflow's env bindings and the lane conditions — and compares each by
# SET EQUALITY with this table. A gate added here therefore FORCES its wiring;
# a gate added to the workflow without a row here fails the same comparisons.
#   <repo variable>|<workflow_dispatch input>|<human label>|<kind>
#   kind=
#     `if`      — a step/job-level `if:` skips the WHOLE gate. A set lane means
#                 the gate was skipped, with nothing in it run.
#     `wrapper` — the skip translates ONLY the checker's exit 1 (undeclared
#                 declarations / fleet violations). Its exit 2 — could not
#                 determine state — is NEVER bypassable, so a lane armed with no
#                 bypass recorded means the checker simply did not return 1.
GATES=(
  "SKIP_DB_HEALTH_GATE|skip-db-health-gate|DB health verification|if"
  "SKIP_PACK_SMOKE|skip-pack-smoke|pack-catalog smoke|if"
  "SKIP_FLY_MACHINES_GUARD|skip-fly-machines-guard|Fly machine orphan/crash-loop guard|wrapper"
  "SKIP_FLY_SECRET_PROVENANCE|skip-fly-secret-provenance|Fly secret provenance|wrapper"
)

# A bypass may stay set this many days before it is a violation. Justification:
# the incident windows these lanes serve are hours-to-days (the #4605 DB-health
# window was a day-scale data-plane restore), so a week is generous enough never
# to nag during a real incident, while a bypass that outlives one working week
# is stale by any reading — and detecting it within a week is the entire point.
WINDOW_DAYS_DEFAULT=7

usage() {
  cat >&2 <<'EOF'
usage:
  deploy-bypass.sh report --key <SKIP_NAME> --input-fired <true|false> \
                          --variable-fired <true|false> [--set-at YYYY-MM-DD] \
                          [--effect <sentence>] [--window-days N] [--today YYYY-MM-DD]
  deploy-bypass.sh audit  [--job <name>] \
                          --state <SKIP_NAME>=<input-fired>/<variable-fired> ... \
                          [--marker-file <path>]
  deploy-bypass.sh expiry [--window-days N] [--today YYYY-MM-DD]

Env: GITHUB_STEP_SUMMARY (append target), DEPLOY_BYPASS_MARKER (per-job audit
marker file), DEPLOY_BYPASS_WINDOW_DAYS (default 7), SKIP_<KEY> and
SKIP_<KEY>_SET_AT (expiry input, supplied by the workflow's `vars` bindings).
EOF
  exit 64
}

die() { printf 'deploy-bypass: %s\n' "$*" >&2; exit 64; }

# The `shift 2 || die …` guards in the three parsers below are load-bearing, not
# style. With a value-taking flag LAST (`report --key`, `audit --state`,
# `expiry --window-days`), `shift 2` FAILS: bash returns 1 and leaves the
# positional parameters UNCHANGED, so (there is no `set -e`) the `while [ $# -gt
# 0 ]` loop re-reads the same `$1` forever and the documented exit-64 usage path
# is never reached. Every value-taking arm therefore fails closed. The crash
# surface is real: `report` is the whole `run:` body of a deploy step and
# `deploy-hosted.yml` sets no `timeout-minutes`, so a typo would spin the runner
# for the 360-minute default instead of failing fast.

# Append to the step summary AND echo to the log (so a local run and the step's
# own log show what the summary will hold). $GITHUB_STEP_SUMMARY is unset
# outside Actions and in a bare local run — never a hard dependency.
emit() {
  printf '%s\n' "$1"
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '%s\n' "$1" >>"$GITHUB_STEP_SUMMARY"
  fi
}

gate_label() { # <SKIP_NAME> -> human label ("" when unknown)
  local row
  for row in "${GATES[@]}"; do
    [ "${row%%|*}" = "$1" ] && { printf '%s' "$(printf '%s' "$row" | cut -d'|' -f3)"; return 0; }
  done
  printf ''
}

gate_input() { # <SKIP_NAME> -> workflow_dispatch input name ("" when unknown)
  local row
  for row in "${GATES[@]}"; do
    [ "${row%%|*}" = "$1" ] && { printf '%s' "$(printf '%s' "$row" | cut -d'|' -f2)"; return 0; }
  done
  printf ''
}

gate_kind() { # <SKIP_NAME> -> "if" | "wrapper" ("" when unknown)
  local row
  for row in "${GATES[@]}"; do
    [ "${row%%|*}" = "$1" ] && { printf '%s' "$(printf '%s' "$row" | cut -d'|' -f4)"; return 0; }
  done
  printf ''
}

today_utc() { date -u +%F; }

# <YYYY-MM-DD> -> epoch seconds at UTC midnight. Non-zero on anything that is
# not a real calendar date. GNU `date -d` first, BSD `date -j -f` second, so the
# harness runs on the macOS dev box too — then the epoch is formatted BACK and
# compared to the input, because both parsers SILENTLY NORMALISE an
# out-of-range day (`2026-02-30` → `2026-03-02` on GNU and BSD alike) and a
# shifted window start is a wrong age, not an invalid one. The round-trip also
# makes the INVALID verdict identical on both platforms.
to_epoch() {
  local d="$1" out back
  case "$d" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
    *) return 1 ;;
  esac
  if out="$(date -u -d "$d 00:00:00" +%s 2>/dev/null)"; then
    :
  elif out="$(date -u -j -f '%Y-%m-%d %H:%M:%S' "$d 00:00:00" +%s 2>/dev/null)"; then
    :
  else
    return 1
  fi
  back="$(date -u -r "$out" +%F 2>/dev/null || date -u -d "@$out" +%F 2>/dev/null)" || return 1
  [ "$back" = "$d" ] || return 1
  printf '%s' "$out"
}

# <start> <today> -> whole days elapsed (start <= today, validated by caller).
days_since() {
  local a b
  a="$(to_epoch "$1")" || return 1
  b="$(to_epoch "$2")" || return 1
  printf '%s' "$(( (b - a) / 86400 ))"
}

# ── report ──────────────────────────────────────────────────────────────────
# The uniform bypass block. Every bypassable gate renders through here; no site
# may hand-roll its own summary (that is the inconsistency #4759 removed).
cmd_report() {
  local key='' input_fired='' variable_fired='' set_at='' effect='' window='' today=''
  local label dispatch_input lanes='' window_note=''

  while [ $# -gt 0 ]; do
    case "$1" in
      --key) key="${2:-}"; shift 2 || die "report: --key requires a value" ;;
      --input-fired) input_fired="${2:-}"; shift 2 || die "report: --input-fired requires a value" ;;
      --variable-fired) variable_fired="${2:-}"; shift 2 || die "report: --variable-fired requires a value" ;;
      --set-at) set_at="${2:-}"; shift 2 || die "report: --set-at requires a value" ;;
      --effect) effect="${2:-}"; shift 2 || die "report: --effect requires a value" ;;
      --window-days) window="${2:-}"; shift 2 || die "report: --window-days requires a value" ;;
      --today) today="${2:-}"; shift 2 || die "report: --today requires a value" ;;
      *) die "report: unexpected argument '$1'" ;;
    esac
  done

  [ -n "$key" ] || die "report: --key is required"
  label="$(gate_label "$key")"
  dispatch_input="$(gate_input "$key")"
  [ -n "$label" ] || die "report: '$key' is not one of the bypassable gates in the GATES table"

  [ "$input_fired" = 'true' ] && lanes='input'
  [ "$variable_fired" = 'true' ] && lanes="${lanes:+$lanes,}variable"
  if [ -z "$lanes" ]; then
    # Unreachable from every call site (each guards on the lane being set): a
    # report with no lane means the workflow wiring is wrong. Loud, and NOT a
    # pass — a broken reporter must not certify a bypass.
    printf '::error::deploy-bypass report for %s fired with NO lane set — the reporting wiring is wrong (input-fired=%s variable-fired=%s)\n' \
      "$key" "${input_fired:-<unset>}" "${variable_fired:-<unset>}" >&2
    emit "## ⛔ Deploy gate bypass report FAILED — \`$key\`"
    emit "- The report ran with **no lane set** — this is a wiring defect in \`deploy-hosted.yml\`, not a gate outcome."
    return 1
  fi

  [ -n "$window" ] || window="${DEPLOY_BYPASS_WINDOW_DAYS:-$WINDOW_DAYS_DEFAULT}"
  case "$window" in
    # A bad window must not be what fails a deploy, so it falls back to the
    # default rather than erroring (the workflow passes no --window-days).
    ''|*[!0-9]*) window="$WINDOW_DAYS_DEFAULT" ;;
  esac

  # The window is only meaningful for the PERSISTENT lane. An input-lane bypass
  # lives for one dispatch run and leaves nothing set, so it carries no age.
  if [ "$variable_fired" = 'true' ]; then
    local age
    [ -n "$today" ] || today="$(today_utc)"
    if [ -z "$set_at" ]; then
      window_note="- **Window start: NOT RECORDED.** Set \`${key}_SET_AT\` (\`gh variable set ${key}_SET_AT --body YYYY-MM-DD\`) so a machine can check the ${window}-day window — a bypass with no start date cannot be aged."
    elif ! age="$(days_since "$set_at" "$today")"; then
      window_note="- **Window start INVALID: \`$set_at\`** — expected \`YYYY-MM-DD\`; the ${window}-day window cannot be checked."
    elif [ "$age" -lt 0 ]; then
      window_note="- **Window start IN FUTURE: \`$set_at\`** — the ${window}-day window cannot be checked."
    elif [ "$age" -gt "$window" ]; then
      window_note="- **⚠️ OVERDUE: set \`$age\` days ago** (\`$set_at\`) — past the ${window}-day window. Clear \`$key\`; \`skip-bypass-expiry\` reports this daily."
    else
      window_note="- **Set \`$age\` days ago** (\`$set_at\`; ${window}-day window) — clear \`$key\` when the incident closes."
    fi
  else
    window_note="- **Lane is per-run** (\`$dispatch_input\` dispatch input) — nothing is left set; no window applies."
  fi

  printf '::warning::%s gate BYPASSED — lane(s): %s (%s). %s. Clear it once the incident is over.\n' \
    "$label" "$lanes" "$key" "$effect" >&2

  emit "## ⛔ Deploy gate BYPASSED — ${label}"
  emit "- **Gate:** \`${key}\` (repo variable) / \`${dispatch_input}\` (dispatch input)"
  emit "- **Lane that fired:** ${lanes} — a **green run does not mean this gate passed**."
  emit "$window_note"
  [ -n "$effect" ] && emit "- **Effect:** ${effect}."
  if [ "$variable_fired" = 'true' ]; then
    emit "- **Action:** clear the repo variable (\`gh variable delete ${key}\`) and delete \`${key}_SET_AT\`; the dispatch input needs no cleanup."
  else
    emit "- **Action:** The dispatch input applies to this run only — nothing is left set to clear."
  fi

  if [ -n "${DEPLOY_BYPASS_MARKER:-}" ]; then
    # Fail LOUD, never open. This marker is what THIS job's audit step reads to
    # tell "a wrapper gate's exit-1 skip was applied" from "the lane was armed
    # but no exit-1 bypass was applied". A dropped write leaves the marker
    # absent, which the audit reads as the second — so the audit states NOT
    # bypassed for a gate the block right above says BYPASSED, the exact
    # contradiction #4759 exists to remove. It is deliberately NOT fatal:
    # failing the step would strand the incident deploy the bypass exists for
    # (the reason `expiry` is a separate workflow). The report block has already
    # certified the bypass; this line says the audit cannot.
    if ! printf '%s\n' "$key" >>"$DEPLOY_BYPASS_MARKER" 2>/dev/null; then
      printf '::warning::deploy-bypass: could not record the audit marker for %s at %s — the audit for this job cannot certify this bypass\n' \
        "$key" "$DEPLOY_BYPASS_MARKER" >&2
      emit "- ⚠️ **Audit marker NOT written** (\`${key}\`, marker \`${DEPLOY_BYPASS_MARKER}\`) — the audit line below cannot certify this bypass."
    fi
  fi
}

# ── audit ───────────────────────────────────────────────────────────────────
# End-of-job. Every gate in the job is stated as bypassed or not, from the LANE
# state (always knowable) plus, for the `wrapper` gates, whether the skip was
# actually applied (the marker the report step writes in that same branch — a
# lane armed with no marker means no exit-1 bypass was applied). The lines state
# the BYPASS, never whether the gate's step executed: an earlier step failing
# can skip a checker, and claiming "it ran" there is a false statement about a
# red run. Whether a gate executed is the job's colour and its step list.
# Running with `if: always()` makes this the authoritative bypass line even when
# an earlier step failed before its own report step could run.
cmd_audit() {
  local job='this job' marker_file='' any=0 spec key label kind in_fired var_fired lanes
  local bypassed=''
  local -a specs=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --job) job="${2:-}"; shift 2 || die "audit: --job requires a value" ;;
      --state) specs+=("${2:-}"); shift 2 || die "audit: --state requires a value" ;;
      --marker-file) marker_file="${2:-}"; shift 2 || die "audit: --marker-file requires a value" ;;
      *) die "audit: unexpected argument '$1'" ;;
    esac
  done
  [ "${#specs[@]}" -gt 0 ] || die "audit: at least one --state <SKIP_NAME>=<input-fired>/<variable-fired> is required"
  [ -n "$marker_file" ] || marker_file="${DEPLOY_BYPASS_MARKER:-}"
  if [ -n "$marker_file" ] && [ -f "$marker_file" ]; then
    bypassed="$(cat "$marker_file")"
  fi

  emit "## Deploy gate audit — ${job}"
  for spec in "${specs[@]}"; do
    case "$spec" in
      *=*/*) ;;
      *) die "audit: malformed --state '$spec' (want <SKIP_NAME>=<input-fired>/<variable-fired>)" ;;
    esac
    key="${spec%%=*}"
    label="$(gate_label "$key")"
    [ -n "$label" ] || label="$key"
    kind="$(gate_kind "$key")"
    in_fired="${spec#*=}"
    in_fired="${in_fired%%/*}"
    var_fired="${spec##*/}"
    lanes=''
    [ "$in_fired" = 'true' ] && lanes='input'
    [ "$var_fired" = 'true' ] && lanes="${lanes:+$lanes,}variable"
    if [ -z "$lanes" ]; then
      emit "- \`${key}\` (${label}) — lane NOT set: **not bypassed** (no bypass lane was set)"
      continue
    fi
    any=1
    if [ "$kind" = 'if' ]; then
      emit "- \`${key}\` (${label}) — **BYPASSED** (lane(s): ${lanes}) — the gate was SKIPPED, not passed"
    elif printf '%s\n' "$bypassed" | grep -qxF "$key"; then
      emit "- \`${key}\` (${label}) — **BYPASSED** (lane(s): ${lanes}) — the checker's exit-1 class was translated for this deploy"
    else
      emit "- \`${key}\` (${label}) — lane(s) ${lanes} armed but **NOT bypassed** — the checker applied no exit-1 bypass (it either returned no violations or did not run; the exit-2 class still blocks)"
    fi
  done
  if [ "$any" -eq 0 ]; then
    emit "- No bypass lane was set for any bypassable deploy gate in this job."
  fi
}

# ── expiry ──────────────────────────────────────────────────────────────────
# The scheduled machine check. Reads the four lanes out of the `vars` context
# (no API, no token scope) and ages each one that is set.
cmd_expiry() {
  local window='' today='' violations=0 key row value value_lc set_at set_at_var
  local age state note
  while [ $# -gt 0 ]; do
    case "$1" in
      --window-days) window="${2:-}"; shift 2 || die "expiry: --window-days requires a value" ;;
      --today) today="${2:-}"; shift 2 || die "expiry: --today requires a value" ;;
      *) die "expiry: unexpected argument '$1'" ;;
    esac
  done
  [ -n "$window" ] || window="${DEPLOY_BYPASS_WINDOW_DAYS:-$WINDOW_DAYS_DEFAULT}"
  case "$window" in
    ''|*[!0-9]*) die "expiry: --window-days must be a positive integer" ;;
  esac
  [ -n "$today" ] || today="$(today_utc)"
  to_epoch "$today" >/dev/null || die "expiry: --today must be YYYY-MM-DD"

  emit "## Deploy bypass expiry check (window: ${window} days, checked ${today})"

  for row in "${GATES[@]}"; do
    key="${row%%|*}"
    set_at_var="${key}_SET_AT"
    # Indirect read: the workflow binds SKIP_<KEY> / SKIP_<KEY>_SET_AT from the
    # `vars` context into this step's env.
    value="${!key-}"
    set_at="${!set_at_var-}"
    # ⛔ GitHub's expression `==` compares strings CASE-INSENSITIVELY, so the
    #    workflow lanes (`vars.SKIP_* == 'true'`) fire on `TRUE`/`True` exactly
    #    as on `true`. This monitor must match the ENGINE, never the other way:
    #    a case-sensitive test here reads `TRUE` as "armed (not set)", leaves
    #    violations=0, goes green forever — and instructs the operator to
    #    delete the only record the bypass could be aged from, while the same
    #    run's deploy summary says BYPASSED. That disagreement IS the
    #    "a failure looks like its success" defect this helper exists to
    #    remove, so normalise the lane before testing it.
    value_lc="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"

    if [ "$value_lc" != 'true' ]; then
      if [ -n "$set_at" ]; then
        printf '::warning::%s is not set (gate is ARMED) but %s=%s is left behind — delete the stale date: gh variable delete %s\n' \
          "$key" "$set_at_var" "$set_at" "$set_at_var" >&2
        emit "- \`${key}\` — **armed** (not set); stale \`${set_at_var}=${set_at}\` should be deleted"
      else
        emit "- \`${key}\` — armed (not set)"
      fi
      continue
    fi

    if [ -z "$set_at" ]; then
      state='**VIOLATION** — bypass set with NO start date'
      note="set \`${set_at_var}\` (\`gh variable set ${set_at_var} --body YYYY-MM-DD\`) so the ${window}-day window can be checked, or clear \`${key}\`"
      violations=$((violations + 1))
      printf '::error::%s=true with no %s — the %s-day bypass window CANNOT be checked. Set %s or clear %s (docs/infra-runbook.md §8.2).\n' \
        "$key" "$set_at_var" "$window" "$set_at_var" "$key" >&2
      emit "- \`${key}\` — ${state}. ${note}."
      continue
    fi

    if ! age="$(days_since "$set_at" "$today")"; then
      state='**VIOLATION** — bypass start date is not a valid YYYY-MM-DD date'
      violations=$((violations + 1))
      printf '::error::%s=%s is not a valid YYYY-MM-DD date — the %s-day bypass window CANNOT be checked.\n' \
        "$set_at_var" "$set_at" "$window" >&2
      emit "- \`${key}\` — ${state} (\`${set_at_var}=${set_at}\`)."
      continue
    fi

    if [ "$age" -lt 0 ]; then
      state='**VIOLATION** — bypass start date is in the FUTURE'
      violations=$((violations + 1))
      printf '::error::%s=%s is in the future — the %s-day bypass window CANNOT be checked.\n' \
        "$set_at_var" "$set_at" "$window" >&2
      emit "- \`${key}\` — ${state} (\`${set_at_var}=${set_at}\`)."
      continue
    fi

    if [ "$age" -gt "$window" ]; then
      violations=$((violations + 1))
      printf '::error::%s has been set for %s days (since %s) — past the %s-day bypass window. Clear it: gh variable delete %s; gh variable delete %s\n' \
        "$key" "$age" "$set_at" "$window" "$key" "${key}_SET_AT" >&2
      emit "- \`${key}\` — **VIOLATION** — **${age} days** set (since ${set_at}), past the ${window}-day window. Clear it."
      continue
    fi

    printf '::warning::%s has been set for %s days (since %s; %s-day window) — clear it when the incident closes.\n' \
      "$key" "$age" "$set_at" "$window" >&2
    emit "- \`${key}\` — set **${age} days** (since ${set_at}) — inside the ${window}-day window"
  done

  if [ "$violations" -eq 0 ]; then
    emit "- **No violation.** Every \`SKIP_*\` lane is inside its window, or the gate is armed."
    return 0
  fi

  emit "- ⛔ **${violations} violation(s).** This run is RED. It is a reminder only — \`skip-bypass-expiry\` is a separate scheduled workflow and **never blocks a deploy**; clear the variable(s) above."
  # GitHub's own scheduled-workflow failure notification is the durable channel
  # for this red; the same age is repeated in every deploy's report block.
  return 1
}

[ $# -ge 1 ] || usage
sub="$1"
shift
case "$sub" in
  report) cmd_report "$@" ;;
  audit) cmd_audit "$@" ;;
  expiry) cmd_expiry "$@" ;;
  -h|--help|help) usage ;;
  *) printf 'deploy-bypass: unknown subcommand %s\n' "$sub" >&2; usage ;;
esac
