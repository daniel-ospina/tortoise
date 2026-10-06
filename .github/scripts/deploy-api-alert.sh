#!/usr/bin/env bash
# ============================================================================
# deploy-api-alert.sh — the out-of-band alert for a FAILED hosted-deploy job
# (#2240).
#
# WHY THIS EXISTS
#   `deploy-hosted.yml`'s `deploy-api` job had NO failure observability at all:
#   no `if: failure()` step anywhere in the workflow, `post-deploy-verify` is
#   skipped exactly when the deploy fails (`if: needs.deploy-api.result ==
#   'success'`), the availability watchdog treats any answer it can get —
#   including a 401/403 from a STALE build — as UP, and `deploy-api` is not a
#   required branch-protection context. So a fail-closed gate can stop the
#   pipeline and NOTHING says so.
#   Measured cost: 2026-08-30T21:58Z → 2026-09-04T11:21Z, 22 consecutive failed
#   runs at one gate, zero notifications, prod served a 4.6-day-old build; and
#   again 2026-09-29 → 2026-10-01. This script is the missing channel.
#
# SCOPE — THE JOB, NOT THE GATE
#   The obvious fix is "alert when the migration-drift gate fails". That is too
#   narrow in two directions.
#   1. Within `deploy-api`, ≥8 steps are fail-closed (dependency parity, verify
#      secrets, Fly secret provenance, migration drift, Fly machines, set
#      secrets, deploy, plus `check-fly-secret-drift.py`'s exit-2 provisioning
#      path), so a per-gate alert would leave the same silence on the other
#      seven. One notifier at the job boundary covers all of them, and the step
#      attribution below is what keeps a per-gate reading possible.
#   2. `packaging-smoke` and `post-deploy-verify` can EACH fail while the other
#      two jobs of the workflow are SKIPPED (`deploy-api` is gated on
#      packaging-smoke's result; `post-deploy-verify` is gated on deploy-api's) —
#      a notifier only inside `deploy-api` would leave a failed pack smoke, which
#      silently blocks the whole deploy, exactly as unobserved as the 4.6-day
#      stall this issue is about. So the SAME script is attached at all three
#      sites. They are mutually exclusive at runtime (a skipped job cannot also
#      fail), so at most one alert fires per run — no duplicate issue, no double
#      page.
#
# THE TWO LEGS, IN THIS ORDER
#   1. THE LEDGER (hard). ONE GitHub issue per failing (job, step), via the
#      shared substrate `.github/scripts/auto-file-issue.sh` (#3907): the title
#      is a STABLE key and the dedupe key, so every later failure of the SAME
#      step comments `Recurrence #N` instead of filing a duplicate. A failed
#      search REFUSES to file and fails the step loudly, so the alert can never
#      go silently deaf.
#      ⛔ The token MUST be the Actions token (`GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}`).
#      A PAT writes as its own owner, the `author:app/github-actions` dedupe
#      search then never matches, and EVERY failing run files a fresh issue —
#      the #2706 duplicate-spam class, silently. The substrate asserts the
#      author from the write's own response and refuses a non-Actions author.
#   2. THE NUDGE (best-effort). One Telegram page through the shared shell
#      sender (`telegram-send.sh`, #4574 item 1), whose delivery contract is
#      transport AND `ok:true`. Best-effort on purpose, mirroring the
#      watchdog's own `page()`: the ledger issue is the standing alert, and a
#      paging-channel outage must not be the reason the finding goes unrecorded
#      — so the page runs AFTER the ledger and its failure only warns.
#      (An issue alone has demonstrably not reached a human here: the watchdog's
#      own header records a sustained 404 for 11h19m that produced ONE incident
#      issue and no human action.)
#
# WHY THE KEY IS PER-STEP, AND WHY IT HOLDS NO RUN ID
#   The acceptance for #2240 is: alert on the FIRST failing run (no N-run
#   threshold) and keep recording every CONSECUTIVE failure of the SAME STEP.
#   A key that embedded `${{ github.run_id }}` would file a new issue per run
#   (the #2706 defect: 59 near-identical issues); a key that held a counter
#   would be reset by `concurrency: cancel-in-progress: true`, and a cancelled
#   run produces no failure to report. So the key names the (workflow, job,
#   step) and nothing that varies per occurrence: the issue thread IS the
#   ledger, and its `Recurrence #N` count is the escalation signal.
#
# WHAT IT DELIBERATELY DOES NOT DO
#   It does not resolve the drift (no prod action, no `migration repair`, no
#   auto-dispatch — the owner ruled those out); it does not change any gate's
#   pass/fail; and it cannot mask the failure it reports, because the job is
#   already red when it runs. It also does not try to detect a stale build —
#   that is a different finding with a different home.
#
# Usage:
#   bash .github/scripts/deploy-api-alert.sh --job deploy-api \
#     --steps "drift=failure verify-secrets=success …" [--drift-report <path>]
#
# Env:  GH_TOKEN (or GITHUB_TOKEN) — MUST be the Actions token, see above
#       GITHUB_REPOSITORY / GITHUB_RUN_ID / GITHUB_SERVER_URL / GITHUB_SHA
#       GITHUB_WORKFLOW_REF, GITHUB_REF_NAME (optional, for the body)
#       TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (optional — the nudge leg)
#       DRIFT_REPORT_FILE_OVERRIDE / --drift-report (the gate's own report)
# Test: bash .github/scripts/deploy-api-alert.test.sh
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${GITHUB_REPOSITORY:-daniel-ospina/tortoise}"
RUN_URL="${GITHUB_SERVER_URL:-https://github.com}/${REPO}/actions/runs/${GITHUB_RUN_ID:-0}"
ALERT_LABEL="${DEPLOY_ALERT_LABEL:-bug}"
# The drift gate writes its own report here (the workflow tees the gate's stdout
# to this path). Only the drift step's failure carries a machine-readable body;
# every other step is described by its name and the run link.
DRIFT_REPORT_FILE="${DRIFT_REPORT_FILE:-}"
DRIFT_STEP_ID="drift"

# The step ids the workflow passes, and the human labels they map to. The ids
# are the KEY's per-step discriminator (they are ours, short and stable); the
# labels are what a reader sees in the issue body. A test pins this table
# against `deploy-hosted.yml` in BOTH directions — every id the workflow passes
# is declared here, every id here is passed by the workflow, and every label is
# the actual `name:` of that step — so the map cannot drift from the workflow.
step_label() { # <step-id> -> human label, or "" when unknown
  case "$1" in
    parity)         printf '%s' 'Dependency parity gate — pyproject ↔ uv.lock ↔ requirements.txt (#494)' ;;
    verify-secrets) printf '%s' 'Verify secrets exist' ;;
    provenance)     printf '%s' 'Check Fly secret provenance (fail-closed)' ;;
    drift)          printf '%s' 'Check migration drift (fail-closed)' ;;
    fly-machines)   printf '%s' 'Check Fly machines (orphan + crash-loop guard, fail-closed)' ;;
    set-secrets)    printf '%s' 'Set all app secrets on Fly.io (keeps in sync with GitHub/Supabase)' ;;
    deploy)         printf '%s' 'Deploy (retry through Fly lease/API races)' ;;
    buildx)         printf '%s' 'Set up Docker Buildx' ;;
    build-image)    printf '%s' 'Build hosted image' ;;
    pack-assert)    printf '%s' 'In-container pack assertion (no boot, no secrets)' ;;
    health-gate)    printf '%s' 'Post-deploy DB health verification (post-release; does not gate the deploy)' ;;
    bypass-report)  printf '%s' 'Bypass report — DB health verification (#1719, #4759)' ;;
    machine-env)    printf '%s' 'Assert the RUNNING machine env honours every fly-toml-env declaration (#5656)' ;;
    *)              printf '' ;;
  esac
}

# What a red in THIS job means for the pipeline. Derived per job, because the
# three alert sites are NOT interchangeable: a skipped `deploy-api` and a failed
# `post-deploy-verify` are opposite conditions (nothing shipped vs a bad release
# already live). A job with no entry gets an honest generic sentence rather than
# a wrong one; a test pins the entry set against the workflow's alert sites.
job_block_note() { # <job>
  case "$1" in
    deploy-api)
      printf '%s' '`post-deploy-verify` is skipped when this job does not succeed (its `if:` requires `needs.deploy-api.result` to be `success`), so a red here means the app did **NOT** flip — nothing has shipped since the last successful run.' ;;
    packaging-smoke)
      printf '%s' '`deploy-api` is gated on this job (its `if:` reads `needs.packaging-smoke.result`), so a red here leaves the deploy **SKIPPED** — the app did **NOT** flip.' ;;
    post-deploy-verify)
      printf '%s' 'This job runs **AFTER** the release: a red here means the release is **LIVE and unhealthy** (there is no rollback), NOT that the deploy failed.' ;;
    *)
      printf '%s' 'The job failed; read the run to see what it gates.' ;;
  esac
}

err()  { echo "::error::$*" >&2; }
warn() { echo "::warning::$*" >&2; }
note() { echo "::notice::$*"; }

# The FIRST step whose outcome is `failure` is the causal one: a job stops at
# the first failing step, so every later step is `skipped` (or `''` when it was
# never reached). Echoes "<id>" or "" when no failed step was reported.
failed_step_from() { # <"id=outcome id=outcome …">
  local pair id outcome
  for pair in $1; do
    id="${pair%%=*}"
    outcome="${pair#*=}"
    if [ "$outcome" = "failure" ]; then
      printf '%s' "$id"; return 0
    fi
  done
  printf ''
}

# The stable dedupe key (issue title). NO run id, NO timestamp, NO count.
alert_key() { # <job> <step-id>
  if [ -n "$2" ]; then
    printf "%s: %s failed at '%s'" "deploy-hosted" "$1" "${2}"
  else
    printf '%s: %s failed' "deploy-hosted" "$1"
  fi
}

usage() {
  cat >&2 <<'USAGE'
usage: deploy-api-alert.sh --job <job-name> --steps "id=outcome …" [--drift-report <path>]

  Files/updates ONE issue for the first step that failed in <job>, then pages
  Telegram best-effort. Requires the GitHub Actions token (see the header).
USAGE
  return 2
}

build_body() { # <out-file> <job> <step-id> <failed-step-raw>
  local out="$1" job="$2" sid="$3" raw="$4" label excerpt
  local failed_names="" pair id outcome pretty
  if [ -n "$raw" ]; then
    for pair in $raw; do
      id="${pair%%=*}"; outcome="${pair#*=}"
      [ "$outcome" = "failure" ] || continue
      pretty="$(step_label "$id")"
      [ -n "$pretty" ] || pretty="$id"
      failed_names="${failed_names}${failed_names:+, }${pretty}"
    done
  fi
  {
    echo "**\`deploy-hosted\` → \`${job}\` failed** — the hosted deploy pipeline is"
    echo "blocked. #2240: this job had no out-of-band observability, so a"
    echo "fail-closed gate could stop the pipeline in silence. This issue is that"
    echo "channel; it is filed and updated automatically."
    echo
    echo "| field | value |"
    echo "|---|---|"
    echo "| workflow | ${GITHUB_WORKFLOW_REF:-deploy-hosted} |"
    echo "| job | \`${job}\` |"
    if [ -n "$sid" ]; then
      echo "| failed step | \`$(step_label "$sid")\` (id \`${sid}\`) |"
    else
      echo "| failed step | **not reported** — the job failed, but no step id was passed (or a step outside the declared set failed); read the run |"
    fi
    if [ -n "$failed_names" ]; then
      echo "| all steps reporting \`failure\` | ${failed_names} |"
    fi
    echo "| commit | \`${GITHUB_SHA:-unknown}\` |"
    echo "| run | ${RUN_URL} |"
    echo
    echo "**The job's colour is the pipeline's colour** — $(job_block_note "$job")"
    echo
    if [ "$sid" = "$DRIFT_STEP_ID" ]; then
      echo "### Migration-drift gate report"
      echo
      echo "The gate's own report (\`check-migration-drift\`) for this run follows verbatim — it"
      echo "names the blocking versions and the ordered remediation. **No production action is"
      echo "taken by this alert, and none should be taken without the owner** (#2240 ruling: a lane"
      echo "must never run DDL or \`migration repair\` against production; a blanket repair hides"
      echo "real drift, #1001)."
      echo
      if [ -n "$DRIFT_REPORT_FILE" ] && [ -s "$DRIFT_REPORT_FILE" ]; then
        # Bounded: an issue body is not a log dump. ⛔ TAKE THE **TAIL**, not the
        # head: the gate prints its context — remote-ahead, non-conforming files,
        # the warn-class preamble — BEFORE the actionable block, and ends with
        # the `BLOCKING` list, the `OUT OF ORDER` subset and the ordered
        # remediation. `head -c` would keep the noise and drop the one thing the
        # alert exists to carry; the run link above is the escape hatch for
        # anything cut (the measured 2026-09-04 report was 509 bytes, so the cap
        # is not normally reached).
        excerpt="$(tail -c 6000 "$DRIFT_REPORT_FILE")"
        echo '```'
        printf '%s' "$excerpt"
        # `tail -c` can start mid-line; a fenced block is still closed below.
        echo
        echo '```'
        if [ "$(wc -c < "$DRIFT_REPORT_FILE")" -gt 6000 ]; then
          echo
          echo "_(report truncated to its LAST 6000 bytes — the gate prints the actionable block last; the full text is in the run link above)_"
        fi
      else
        echo "⚠️ **The gate's report file was not readable** (\`${DRIFT_REPORT_FILE:-<unset>}\`)."
        echo "The blocking versions and the remediation are in the run's \`Check migration drift"
        echo "(fail-closed)\` step log — this alert deliberately does NOT re-run the gate, because a"
        echo "second reading of prod can name a different blocking set than the run that failed."
      fi
      echo
    fi
    echo "---"
    echo
    echo "**One issue per failing step (#2240 acceptance).** Subsequent failures of the SAME step"
    echo "comment here as \`Recurrence #N\` instead of filing another issue, so the occurrence count"
    echo "on this thread is the streak length. A failure at a DIFFERENT step files its own issue."
    echo "Close this issue when the streak ends."
  } > "$out"
  [ -s "$out" ] || { err "built an empty alert body — refusing to file"; return 1; }
}

main() {
  local job="" steps="" body_file="" key="" sid="" ledger_ok=0 page_text=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --job)          job="${2:-}"; shift 2 ;;
      --steps)        steps="${2:-}"; shift 2 ;;
      --drift-report) DRIFT_REPORT_FILE="${2:-}"; shift 2 ;;
      -h|--help)      usage; return 0 ;;
      *) err "unknown argument: $1"; usage; return 2 ;;
    esac
  done
  [ -n "$job" ] || { err "--job is required (it is part of the dedupe key)"; usage; return 2; }

  if [ -z "$(printf '%s' "${GH_TOKEN:-${GITHUB_TOKEN:-}}")" ]; then
    err "GH_TOKEN is not set — refusing to run an alert that cannot file or comment (a deaf alert is worse than none). Bind GH_TOKEN: \${{ secrets.GITHUB_TOKEN }} — NOT a PAT."
    return 1
  fi

  sid="$(failed_step_from "$steps")"
  key="$(alert_key "$job" "$sid")"
  body_file="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/deploy-api-alert-body.md"
  build_body "$body_file" "$job" "$sid" "$steps"

  # The ledger is the alert. Sourced (AUTO_FILE_LIB_ONLY) so the substrate's
  # own guards — Actions-token assertion, exact-title check, fail-closed search
  # — apply unchanged and are exercised by its harness too.
  AUTO_FILE_LIB_ONLY=1 . "${SCRIPT_DIR}/auto-file-issue.sh"
  if af_file_or_comment "$key" "$body_file" "$ALERT_LABEL"; then
    ledger_ok=1
    note "alert ledger updated for '${key}'"
  else
    err "the alert ledger could NOT be updated (see the substrate's error above) — the finding may be UNRECORDED for '${key}'"
  fi

  # The nudge. Best-effort by design (see the header); never fail the step on a
  # paging-channel problem, but never hide it either.
  if [ "$ledger_ok" = "1" ]; then
    page_text="🔴 deploy-hosted: ${job} failed at '$(step_label "$sid")'. ${RUN_URL}"
    if [ -z "$sid" ]; then
      page_text="🔴 deploy-hosted: ${job} failed. ${RUN_URL}"
    fi
    if [ -z "${TELEGRAM_CHAT_ID:-}" ] || [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
      warn "telegram page skipped (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set) — the alert ledger issue is the standing alert"
    else
      TELEGRAM_SEND_LIB_ONLY=1 . "${SCRIPT_DIR}/telegram-send.sh"
      if tg_send "$TELEGRAM_CHAT_ID" "$(printf '%s' "$page_text" | head -c 300)"; then
        note "telegram page delivered"
      else
        warn "telegram page NOT delivered (see above) — the alert ledger issue is the standing alert"
      fi
    fi
  fi

  [ "$ledger_ok" = "1" ] || return 1
  return 0
}

if [ "${DEPLOY_ALERT_LIB_ONLY:-0}" != "1" ]; then
  main "$@"
fi
