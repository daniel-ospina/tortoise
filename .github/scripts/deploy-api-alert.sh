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
#   would be reset by a `concurrency` that cancels in progress (`deploy-hosted`
#   did until #10155, which set `cancel-in-progress: false`), and a cancelled
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
# ── THE RESOLVE LEG (`--resolve`, #8841) ──────────────────────────────────
#   Filing without closing is a ledger that can only grow. Measured: #7634 was
#   filed 2026-10-07T20:11Z, **21 runs then SUCCEEDED** on that very key
#   (2026-10-09T23:23Z … 2026-10-11T00:36Z) and ten days later it was still OPEN
#   with 238 `Recurrence` comments — i.e. for that window the standing alert
#   claimed a condition that was no longer true, and every later recurrence was
#   indistinguishable noise. The body of this script always said "close this
#   issue when the streak ends"; nothing mechanical ended it.
#
#   So the SAME per-step outcomes that attribute a failure now also RETIRE it:
#   every step that reported `success` in this run resolves its own key, and the
#   job-level fallback key (`… failed`, no step) resolves only when `--job-status`
#   is `success` — i.e. when the whole job is green.
#
#   ⛔ IT RESOLVES ON EVIDENCE, NOT ON ELECTION. A step that is `skipped` or
#   `failure` is never resolved (a run that dies early skips the later steps; a
#   skipped step is not a passing one). Only the step's OWN `success` in THIS run
#   closes its key, which is the same per-step attribution the alert path uses.
#
#   ⛔ IT NEVER FAILS THE RUN. This leg runs on the GREEN path (and on a red run
#   whose earlier step recovered), so a bookkeeping failure must not turn a good
#   deploy red — a red here would be a FALSE deploy failure, and the alert step's
#   `--steps` list does not include this step, so the job-level fallback key would
#   then file an alert claiming the deploy failed. Every failure in this mode is a
#   `::warning::` with exit 0; ONLY a broken invocation (exit 2) is loud, because
#   a permanently-wrong invocation must not silently never resolve.
#
# Usage:
#   bash .github/scripts/deploy-api-alert.sh --job deploy-api \
#     --steps "drift=failure verify-secrets=success …" [--drift-report <path>]
#   bash .github/scripts/deploy-api-alert.sh --job deploy-api --resolve \
#     --job-status <success|failure|cancelled> --steps "drift=success …"
#
# Env:  GH_TOKEN (or GITHUB_TOKEN) — MUST be the Actions token, see above
#       GITHUB_REPOSITORY / GITHUB_RUN_ID / GITHUB_SERVER_URL / GITHUB_SHA
#       GITHUB_WORKFLOW_REF, GITHUB_REF_NAME (optional, for the body)
#       TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (optional — the nudge leg)
#       DRIFT_REPORT_FILE (or --drift-report) — the gate's own report
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
# The resolve leg (#8841). RESOLVE_MODE=1 = retire the keys whose steps passed;
# JOB_STATUS is the workflow's `${{ job.status }}`, the ONLY evidence that the
# job as a whole is green (and therefore that the job-level fallback key may be
# resolved).
RESOLVE_MODE=0
JOB_STATUS=""

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
      # ⛔ This must be true for EVERY step in the job, not just the early ones.
      # `Deploy` runs BEFORE the `if: always()` gate audit, and the alert is the
      # job's LAST step — so a red here can happen with a release already created
      # (`flyctl deploy --wait-timeout 420` may have released before failing, and
      # the audit can fail on its own after a clean deploy). The old text asserted
      # "nothing has shipped since the last successful run", which is FALSE in those
      # cases and would tell an on-call reader a live, unverified release does not
      # exist (round-6 review, P3). So the invariant is stated, and the shipped-or-
      # not question is deferred to the `failed step` row.
      printf '%s' '`post-deploy-verify` is skipped when this job does not succeed (its `if:` requires `needs.deploy-api.result` to be `success`), so a red here means the release was **not** verified end-to-end — see the `failed step` row below: a failure **before** `Deploy` shipped nothing, **at or after** it a release may be live but unverified.' ;;
    packaging-smoke)
      printf '%s' '`deploy-api` is gated on this job (its `if:` reads `needs.packaging-smoke.result`), so a red here leaves the deploy **SKIPPED** — the app did **NOT** flip.' ;;
    post-deploy-verify)
      printf '%s' 'This job runs **AFTER** the release: a red here means the release is **LIVE and unhealthy** (there is no rollback), NOT that the deploy failed.' ;;
    *)
      printf '%s' 'read the run to see what it gates (no note is declared for this job)' ;;
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
       deploy-api-alert.sh --job <job-name> --resolve --job-status <s> --steps "id=outcome …"

  Default: files/updates ONE issue for the first step that failed in <job>, then
  pages Telegram best-effort. Requires the GitHub Actions token (see the header).

  --resolve (#8841): closes the ledger issue of every step that reported
  `success` in this run (plus the job-level key when --job-status is `success`),
  so a recovered fault cannot leave a stale OPEN alert behind. Never fails the
  run: every failure here is a warning, except a broken invocation (exit 2).
USAGE
  return 2
}

build_body() { # <out-file> <job> <step-id> <failed-step-raw>
  local out="$1" job="$2" sid="$3" raw="$4" label excerpt
  local raw_tail squeeze_incomplete
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
    # ⛔ The opener carries the JOB'S OWN meaning, never a hard-coded claim. "the
    # hosted deploy pipeline is blocked" is true for `deploy-api` and
    # `packaging-smoke` and FALSE for `post-deploy-verify`, which runs AFTER the
    # release — a red there means the release is LIVE and unhealthy, not blocked.
    # `job_block_note` is the single source, so the opener and the table cannot
    # drift apart. (Round-4 review, P2-1.)
    echo "**\`deploy-hosted\` → \`${job}\` failed** — $(job_block_note "$job")"
    echo
    echo "#2240: this job had no out-of-band observability, so a fail-closed gate"
    echo "could stop the pipeline in silence. This issue is that channel; it is"
    echo "filed and updated automatically."
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
    if [ "$sid" = "$DRIFT_STEP_ID" ]; then
      echo "### Migration-drift gate report"
      echo
      # ⛔ NOT "follows verbatim" (#12200). This intro is emitted for EVERY drift-step body,
      # before the report is even READ, so an unconditional claim about what follows is false
      # in every state that carries no report (not readable, 0-byte, blank, fallback-empty)
      # and in the states that carry one but not verbatim (the ASCII fallback drops bytes,
      # the 6000-byte excerpt is a TAIL, the squeeze drops a split character). It was the
      # louder sibling of the arm sentence fixed further down: same class, same cause — a
      # sentence authored at the BRANCH site describing an ARTIFACT decided later. Write the
      # claim about the artifact, never about the code path that produced it. Pinned by
      # case 17i/17c: the phrase is ABSENT from every report-less body, AND the presence
      # claim must stay CONDITIONED (a `<verb> below;` with no condition reds) — pinning
      # only the deleted phrase left the four-word deletion of `when it can be carried`
      # green, measured at review round 3.
      echo "The gate's own report (\`check-migration-drift\`) — the one that names the blocking"
      echo "versions and the ordered remediation — is reproduced below when it can be carried; any of"
      echo "the 6000-byte cut, a character dropped by the squeeze, or the bytes dropped by the ASCII"
      echo "fallback is declared beside it. **No production action is"
      echo "taken by this alert, and none should be taken without the owner** (#2240 ruling: a lane"
      echo "must never run DDL or \`migration repair\` against production; a blanket repair hides"
      echo "real drift, #1001)."
      echo
      # `-e` so a 0-byte report is not described as "not readable" (it is readable and
      # empty — it lands in the "no excerpt-able text" arm), and `! -p` so a FIFO cannot
      # enter the read: `tail` would block in open(2) until a writer appears, hanging the
      # alert with no ledger entry and no exit status (measured: SIGTERM at 6 s). No caller
      # produces a FIFO; the condition keeps the next one honest rather than silent.
      if [ -n "$DRIFT_REPORT_FILE" ] && [ -e "$DRIFT_REPORT_FILE" ] && [ ! -p "$DRIFT_REPORT_FILE" ]; then
        # Bounded: an issue body is not a log dump. ⛔ TAKE THE **TAIL**, not the
        # head: the gate prints its context — remote-ahead, non-conforming files,
        # the warn-class preamble — BEFORE the actionable block, and ends with
        # the `BLOCKING` list, the `OUT OF ORDER` subset and the ordered
        # remediation. `head -c` would keep the noise and drop the one thing the
        # alert exists to carry; the run link above is the escape hatch for
        # anything cut (the measured 2026-09-04 report was 509 bytes, so the cap
        # is not normally reached).
        # `tail -c` cuts at a BYTE, so it can land inside a multibyte character.
        # TWO paths follow and NEITHER can put an undecodable byte in an issue body:
        # the SQUEEZE below drops the split sequence, so the pasted excerpt is valid
        # UTF-8; the FALLBACK (further down, when the squeeze yields NOTHING) drops
        # every non-ASCII byte, so its output is ASCII and therefore valid UTF-8 too.
        # Both paths DECLARE what they dropped, and the state that carries NOTHING says so
        # (#12200), so the body never reads as a report it does not carry. The fence is then
        # made longer than any backtick run in the excerpt: the report carries repo-controlled
        # text (migration filenames, version names), and a report containing ``` would close the
        # block early and render the rest as live markdown. (Round-5 review, P3-2.)
        #
        # ⛔ #12077 — the SQUEEZE'S EXIT STATUS IS NOT THE ALERT'S, and it is not
        # EVIDENCE either: on glibc 2.43 (`ubuntu-26.04`, the runner image) `iconv -c`
        # emits the convertible bytes and STILL exits 1 for the sequence it dropped
        # (measured: 5999 bytes, rc=1), where glibc 2.39 exits 0 for the same input.
        # Under `set -euo pipefail` that status aborted the whole alert, so
        # `ops-harnesses` went red — it is not a required branch-protection context
        # and is not in `.mergify.yml`'s queue/merge conditions, so its red does not
        # block the rail directly; what it does is redden the PR's check list and
        # stall a lane that waits for green — over a DECORATIVE excerpt, while the
        # ledger entry this script exists to file was never written. The excerpt is
        # best-effort by construction, so its status is caught here and the record
        # still lands — the same ordering rule as cases 11/12: the record comes
        # first, the nudge second, and neither may be lost to decoration.
        #
        # Whether a character WAS dropped is measured, never inferred from the status:
        # the raw tail is captured once and the squeezed bytes are compared against it,
        # so the disclosure below fires on BOTH glibc generations (2.39 drops the split
        # sequence silently, rc=0).
        #
        # ⚠️ NOT checked, stated rather than implied: a NUL byte is valid UTF-8 and is
        # stripped by the command substitution before the comparison, so a report
        # carrying NULs is pasted with them removed and this measure cannot see it.
        # `tail` is guarded: a report that cannot be RE-READ (a directory, or a file that
        # vanished between the guard and the read) lands in the "no excerpt-able text"
        # branch below instead of aborting the alert under `set -e` — the #12077 class at
        # this line too. Pinned by case 17d, which passes a DIRECTORY (an `-e`-true,
        # unreadable path).
        raw_tail="$(tail -c 6000 "$DRIFT_REPORT_FILE" 2>/dev/null)" || raw_tail=""
        excerpt=""
        squeeze_incomplete=0
        # `2>/dev/null`: a converter that is absent prints its own "command not found" or
        # refusal on stderr, which lands in the job log beside an alert that went out
        # perfectly well. ⛔ THE HISTORY, CHECKED RATHER THAN ASSUMED (#12200): the
        # pre-#12110 form of this statement DID suppress it
        # (`… | iconv -f UTF-8 -t UTF-8 -c 2>/dev/null || true`, `49cf519354`), and
        # #12110's rewrite of the squeeze (`6e5ff01625`) did not carry the suppression
        # over — no merge dropped it, and no commit has touched this file on main since.
        # So this line ADDS it relative to main: one spurious diagnostic per alert on a
        # runner without `iconv` (recorded on #12200). An earlier draft of this comment
        # said "main already suppressed it; a merge dropped it", which is the same class
        # this change removes — a sentence stating something the artifact does not show.
        if ! excerpt="$(printf '%s' "$raw_tail" | iconv -f UTF-8 -t UTF-8 -c 2>/dev/null)"; then
          : # status only — the bytes `iconv` could convert have already been emitted
        fi
        if [ -z "$raw_tail" ]; then
          # A report that is nothing but line endings or NUL bytes captures as EMPTY.
          # Claiming "could not be byte-squeezed" there (or pasting an empty block under
          # a heading that promises the report) would be two false statements; say only
          # what is true.
          #
          # ⛔ AND SAY WHICH BYTES WERE READ (review round 5, P3): the tail is only the
          # report's LAST 6000 bytes, so a 15,400-byte report whose final bytes are blank
          # holds plenty of text the alert never looked at. Calling that "the report file
          # held no excerpt-able text" is false about the file — and it left the 6000-byte
          # cut UNDECLARED, which made the section intro's "any of the 6000-byte cut … is
          # declared beside it" false in this ONE state. Measure the size and name both
          # facts without diagnosing which of them caused the empty capture.
          report_bytes="$(wc -c < "$DRIFT_REPORT_FILE" 2>/dev/null)" || report_bytes=""
          case "${report_bytes:-}" in '' | *[!0-9]*) report_bytes=0 ;; esac
          if [ "$report_bytes" -gt 6000 ]; then
            echo "⚠️ **The report's last 6000 bytes held no excerpt-able text** (nothing but line endings or NUL bytes, or it could not be re-read); the report itself is ${report_bytes} bytes, and only that tail is read — the gate's own output is in the run link above."
            echo
          else
            echo "⚠️ **The report file held no excerpt-able text** (nothing but line endings or NUL bytes, or it could not be re-read); the gate's own output is in the run link above."
            echo
          fi
        elif [ -z "$excerpt" ]; then
          # An iconv that is absent, or that refuses without emitting anything, would
          # otherwise paste an EMPTY block under a heading that promises the report.
          # TWO properties must hold at once here, and neither is optional:
          #
          #   (a) the body is VALID UTF-8. The 6000-byte cut can land inside a
          #       multibyte character, and this branch is reached precisely when no
          #       converter is available to drop it — so the RAW tail cannot be pasted:
          #       an undecodable issue body is not a report. `tr -d '\200-\377'` is
          #       coreutils, its output is pure ASCII, hence valid UTF-8.
          #   (b) the body never OVERSTATES what it carries. Dropping every non-ASCII
          #       byte loses more than the split character, so the loss is DECLARED
          #       rather than absorbed — a silent empty (or silently mangled) block
          #       reads as "the gate reported nothing".
          #
          # Both are pinned: "the fallback body is valid UTF-8 (non-ASCII bytes are
          # dropped, never split)" and "does NOT claim a squeeze it did not perform".
          # ⛔ `|| true` is a GUARD, not decoration: under `set -euo pipefail` this is a
          # plain assignment, so a `tr` that fails would abort `build_body` BEFORE
          # anything is filed — a fail-CLOSED notifier, i.e. the very silence #2240
          # exists to remove. The guard is on main and must stay there: `origin/main:368`
          # carries the same `|| true`, it has survived every rewrite of this statement since
          # 49cf519354, and case 17h pins it. (An earlier draft of this comment said a merge
          # dropped main's `|| true` — false, and the same class this change removes: a
          # sentence stating a mechanism the artifact does not show.)
          excerpt="$(printf '%s' "$raw_tail" | LC_ALL=C tr -d '\200-\377' || true)"
          # ⛔ The fallback has a SECOND empty state, and the sentence below is a claim
          # about an artifact: a report whose excerpt-able text is entirely non-ASCII
          # (e.g. `→→→`) squeezes to NOTHING, so the fenced block further down is skipped
          # — and an unconditional "it is pasted with its non-ASCII bytes dropped" then
          # promises an excerpt the body does not carry (#12200, P3-1 of PR #12110's
          # review: the on-call reader is told the degraded report is here when the whole
          # report is only at the run link). One sentence per state, each conditioned on
          # what was actually produced, is the shape that cannot diverge again. Pinned by
          # case 17i, whose not_contains row reds if this condition is removed.
          # ⛔ NAME THE STATE, NOT ONE CAUSE (review round 5, note 1). An EMPTY `iconv`
          # output has three reachable causes: no `iconv` at all, one that refused/failed, and
          # — measured — one that is perfectly healthy and emits NOTHING because every byte of
          # the tail was undecodable while the ASCII fallback still recovered text. Naming only
          # the first two told the on-call reader the converter was missing on a host where it
          # had just worked, so both sentences below enumerate the state inclusively.
          if [ -n "$excerpt" ]; then
            echo "⚠️ **The report could not be byte-squeezed** (\`iconv\` is unavailable, refused to emit, or emitted nothing it could convert); it is pasted with its non-ASCII bytes dropped, so any non-ASCII character in it is LOST — the run link above holds the full text."
          else
            # ⛔ SAY ONLY WHAT IS TRUE IN THIS STATE, not what the BRANCH implies. `excerpt` is
            # empty here for TWO reasons and the body cannot tell them apart: the tail may be
            # entirely non-ASCII, OR the `tr` fallback itself may have failed (it is guarded by
            # `|| true` precisely so a failing tr cannot go deaf — case 17h). An earlier version
            # of this sentence asserted the first cause ("every byte … is non-ASCII"), which is
            # FALSE in the second state: measured in case 17h's own state (broken iconv + broken
            # tr, an ASCII report), the body claimed every byte of an ASCII report was non-ASCII
            # — the exact class this whole change exists to remove, reintroduced one branch down.
            # The reader's action is the same in both states, so name the state, not the cause.
            echo "⚠️ **The report could not be byte-squeezed** (\`iconv\` is unavailable, refused to emit, or emitted nothing it could convert), and the ASCII fallback produced no excerpt either — so NOTHING is pasted below; the full report is at the run link above."
          fi
          echo
        elif [ "$(printf '%s' "$excerpt" | wc -c)" -lt "$(printf '%s' "$raw_tail" | wc -c)" ]; then
          squeeze_incomplete=1
        fi
        if [ -n "$excerpt" ]; then
          fence='```'
          while grep -qF -- "$fence" <<<"$excerpt"; do
            printf -v fence '%s`' "$fence"
          done
          echo "$fence"
          printf '%s' "$excerpt"
          # `tail -c` can start mid-line; a fenced block is still closed below.
          echo
          echo "$fence"
          if [ "$(wc -c < "$DRIFT_REPORT_FILE")" -gt 6000 ]; then
            echo
            echo "_(report truncated to its LAST 6000 bytes — the gate prints the actionable block last; the full text is in the run link above)_"
          fi
          if [ "$squeeze_incomplete" = "1" ]; then
            echo
            echo "_(the excerpt is NOT byte-identical to the report: characters that are not valid UTF-8 were dropped — the 6000-byte cut can split one, and the report itself may hold others — so it can be shorter than the report)_"
          fi
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

# ── the resolve leg (#8841) ────────────────────────────────────────────────
# Retire the ledger keys whose steps PASSED in this run. Reads the same
# `id=outcome …` map the alert path reads, so a step can never be alerted on a
# key it cannot also resolve.
#
# Returns 0 ALWAYS (except a caller mistakes it for the alert path): this runs on
# the green path, and a bookkeeping failure must never redden a deploy that
# actually shipped — see the header, "IT NEVER FAILS THE RUN". Failures are
# surfaced as `::warning::` on every run, so a stuck resolver is visible in the
# run annotations without ever being a false deploy failure.
resolve_keys() { # <job> <job-status> <"id=outcome …">
  local job="$1" status="$2" steps="$3" pair id outcome key why label
  local resolved=0 failures=0 tried=0
  AUTO_FILE_LIB_ONLY=1 . "${SCRIPT_DIR}/auto-file-issue.sh"
  for pair in $steps; do
    id="${pair%%=*}"; outcome="${pair#*=}"
    # ONLY a step's own `success` in THIS run retires its key. `skipped` is not
    # a pass (a run that fails early skips everything below it) and `failure`
    # is the streak itself.
    [ "$outcome" = "success" ] || continue
    [ -n "$id" ] || continue
    tried=$((tried + 1))
    key="$(alert_key "$job" "$id")"
    label="$(step_label "$id")"; [ -n "$label" ] || label="$id"
    why="${label} (step id \`${id}\`) reported success in this run, so its failure streak has ended."
    if af_resolve "$key" "$why"; then
      resolved=$((resolved + 1))
    else
      failures=$((failures + 1))
      warn "could NOT resolve '${key}' — the ledger issue stays OPEN (it will keep collecting Recurrence comments for a fault that is gone); see the substrate's error above"
    fi
  done
  # The job-level fallback key (no step id) is the key used when no step id was
  # reported, so it is only retired when the JOB ITSELF is green — a step's
  # `success` says nothing about a job that later died.
  if [ "$status" = "success" ]; then
    tried=$((tried + 1))
    key="$(alert_key "$job" "")"
    why="The whole ${job} job reported success (\`job.status\`), so the job-level failure key has ended its streak."
    if af_resolve "$key" "$why"; then
      resolved=$((resolved + 1))
    else
      failures=$((failures + 1))
      warn "could NOT resolve '${key}' — the ledger issue stays OPEN (see the substrate's error above)"
    fi
  fi
  if [ "$failures" -gt 0 ]; then
    warn "resolve leg: ${resolved}/${tried} ledger key(s) retired, ${failures} could NOT be — a stale OPEN alert outlives the fault it describes, and this leg deliberately does not fail the run"
  else
    note "resolve leg: ${resolved}/${tried} ledger key(s) retired for job '${job}' (job status: ${status:-unset})"
  fi
  return 0
}

main() {
  local job="" steps="" body_file="" key="" sid="" ledger_ok=0 page_text=""
  while [ $# -gt 0 ]; do
    # A value-taking flag with no value must be a usage error WITH a message.
    # A bare `shift 2` fails under `set -e` and aborts with exit 1 and NO output,
    # before `usage` can run (round-5 review, P3-1).
    case "$1" in
      --job|--steps|--drift-report|--job-status)
        if [ "$#" -lt 2 ]; then
          err "$1 needs a value"
          usage || true
          return 2
        fi ;;
    esac
    case "$1" in
      --job)          job="${2:-}"; shift 2 ;;
      --steps)        steps="${2:-}"; shift 2 ;;
      --drift-report) DRIFT_REPORT_FILE="${2:-}"; shift 2 ;;
      --resolve)      RESOLVE_MODE=1; shift ;;
      --job-status)   JOB_STATUS="${2:-}"; shift 2 ;;
      -h|--help)      usage || true; exit 0 ;;
      *) err "unknown argument: $1"; usage; return 2 ;;
    esac
  done
  [ -n "$job" ] || { err "--job is required (it is part of the dedupe key)"; usage; return 2; }

  if [ "$RESOLVE_MODE" = "1" ]; then
    case "$JOB_STATUS" in
      ''|success|failure|cancelled) ;;
      *) err "--job-status must be one of success|failure|cancelled (got '${JOB_STATUS}')"; usage; return 2 ;;
    esac
    if [ -z "$(printf '%s' "${GH_TOKEN:-${GITHUB_TOKEN:-}}")" ]; then
      # Warn, never abort: this leg runs on the GREEN path, and a missing token
      # must not be reported as a failed deploy (the alert path below keeps its
      # hard refusal — a red run that cannot record its finding is a real defect).
      warn "GH_TOKEN is not set — the resolve leg cannot retire any ledger key; a recovered fault's alert issue stays OPEN. Bind GH_TOKEN: \${{ secrets.GITHUB_TOKEN }}."
      return 0
    fi
    resolve_keys "$job" "$JOB_STATUS" "$steps"
    return 0
  fi
  [ -z "$JOB_STATUS" ] || { err "--job-status is only meaningful with --resolve"; usage; return 2; }

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
