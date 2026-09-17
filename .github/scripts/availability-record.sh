#!/usr/bin/env bash
# ============================================================================
# availability-record.sh — RECORD-ONLY rolling availability baseline (#3810).
#
# ⛔ THIS IS AN INSTRUMENT, NOT A GATE.
# It never restarts anything, never pages, and NEVER exits non-zero because a
# figure is low. It measures the *probe*, not the service. A non-zero exit here
# means the instrument itself could not run or could not publish its record —
# never that availability was poor.
#
# ⛔ PUBLICATION IS AN OWNER DECISION. This script writes into ONE internal
# GitHub issue (a rolling record) and nothing else. It does not publish a status
# page, an SLA, a target, or anything customer-facing, and it deliberately
# defines none. The measured record is evidence for a human decision about
# whether (and when) the probe may gate anything.
#
# WHY THIS EXISTS (#3810)
# -----------------------
# Objective 8 adopted 99.5%/30 days as a probe threshold, *preceded by a
# 4-week record-only baseline* so the probe is proven not to lie before it
# gates. That baseline had no instrument. This is it.
#
# The probe (`.github/scripts/availability-watchdog.sh`, workflow
# `availability-watchdog.yml`) is an ALERTER: it files/updates/closes issues and
# can restart the Fly machine. It keeps NO score. Measured 2026-09-17 over the
# whole run population (452 runs; the workflow was created 2026-09-13T03:15:01Z):
#
#   * The probe fires ~96x/day, not 288 — 33% of its own cron. 452 samples were
#     delivered where 1356 were expected over 113.0h. During the one incident it
#     measured, delivery was 36%. The REAL resolution is ~15 minutes, not 5.
#   * Its one recorded incident (#3637, 2026-09-16T07:34:59Z→18:54:01Z, 48
#     failing runs) was kind=DEGRADED — the app ANSWERED (http_code=404, body
#     `{"detail":"Not Found"}`), it was not silent. The ledger records
#     `restarts=` = none.
#
# THREE BLIND SPOTS, MADE VISIBLE HERE (never averaged away):
#   1. 5-minute granularity cannot see a shorter outage, and the cadence is
#      really ~33%. → Every availability figure is printed with the delivered
#      sample count and the cadence factor (delivered/intended) IMMEDIATELY
#      beside it. A percentage without them is a defect, not a formatting
#      choice. Zero-delivery days are rows too: "no observation" must never
#      read as "available" (a gap is not an uptime sample).
#   2. No score exists. → This produces one, per UTC day, with `failures` and
#      the sample resolution that qualifies it.
#   3. The watchdog's self-healing restart leg truncates DOWN outages longer
#      than SUSTAINED_DOWN_MINUTES (10): a later UP can mean "the probe saw it
#      recover", not "it was available all along". → `restarts=` is read from
#      each ledger incident and surfaced, so the record can be read with and
#      without the self-healed intervals.
#
# DOWN and DEGRADED ARE NEVER SUMMED. They are different product failures (no
# answer vs answered wrongly). They get SEPARATE columns.
#
# READ-ONLY CONTRACT
#   * Consumes `gh api` reads (run list, issue search, issue read).
#   * Writes exactly ONE issue body — the record issue — via PATCH, or creates
#     that issue when absent. It NEVER touches an incident issue, never closes
#     anything, never comments anywhere.
#   * Does not read or write the watchdog's script or workflow. Changing
#     either would be a behaviour change to a live monitor and is out of scope.
#
# Env / args
#   $1 | AVAILABILITY_RECORD_DAYS   window in days (default 28)
#   AVAILABILITY_RECORD_PRINT_ONLY=1 | --print-only
#                                   render + print the record, publish nothing
#   RECORD_NOW_EPOCH                test seam: pin "now" (epoch seconds)
#   RECORD_CRON_PER_DAY             test seam: cron intent (default 288)
#   RECORD_REPO                     override owner/name (default GITHUB_REPOSITORY)
#   RECORD_WORKFLOW                 workflow file (default availability-watchdog.yml)
#
# Harness: .github/scripts/availability-record.test.sh (stubs `gh`, no network,
# CI job `availability-record`). Run: bash .github/scripts/availability-record.test.sh
# ============================================================================
set -uo pipefail

# ── config ──────────────────────────────────────────────────────────────────
REPO="${RECORD_REPO:-${GITHUB_REPOSITORY:-}}"
WATCHDOG_WORKFLOW="${RECORD_WORKFLOW:-availability-watchdog.yml}"
# The cron intent: every 5 minutes = 288/day. This is the DENOMINATOR of the
# cadence factor, i.e. what the probe was supposed to deliver.
CRON_PER_DAY="${RECORD_CRON_PER_DAY:-288}"
# Test seam for a deterministic "now".
RECORD_NOW_EPOCH="${RECORD_NOW_EPOCH:-}"

# The ledger marker the watchdog writes into every incident body (its
# INCIDENT_STATE_MARKER). This script READS issues carrying it; it never writes
# it. Kept as a literal so it is a read-only consumer of an existing contract.
INCIDENT_STATE_MARKER='<!-- availability-watchdog-state -->'
# The marker this recorder writes into the ONE record issue. Distinct from the
# incident marker, so the record issue can never be mistaken for an incident
# (and the watchdog can never adopt it).
RECORD_STATE_MARKER='<!-- availability-record-state -->'
RECORD_TITLE='[monitor] availability record — record-only baseline (NOT a gate)'
# The LOOSE title term the search sends (`in:title`), mirroring the watchdog: a
# full-title query containing an em-dash and parentheses can tokenize to a miss,
# and a miss is the duplicate-spam direction. The EXACT title is re-checked
# client-side before anything is adopted.
RECORD_TITLE_MARKER='[monitor] availability record'

# ── logging (ALL to stderr; stdout carries the record) ──────────────────────
log()  { echo "$*" >&2; }
if [ -n "${GITHUB_ACTIONS:-}" ]; then
  note() { echo "::notice::$*" >&2; }
  warn() { echo "::warning::$*" >&2; }
  fail() { echo "::error::$*" >&2; }
else
  note() { echo "NOTICE: $*" >&2; }
  warn() { echo "WARNING: $*" >&2; }
  fail() { echo "ERROR: $*" >&2; }
fi

RUN_TMP="$(mktemp -d)"
trap 'rm -rf "$RUN_TMP"' EXIT

# ── numeric + time helpers (same normalizers as the watchdog: the ledger body
#    is human-editable, and bash arithmetic aborts on values like `08`) ──────
to_int() { # <raw> <default>
  local raw digits
  raw="$(printf '%s' "${1:-}" | tr -dc '0-9')"
  [ -n "$raw" ] || { printf '%s' "$2"; return 0; }
  digits="${#raw}"
  [ "$digits" -le 12 ] || { printf '%s' "$2"; return 0; }
  raw="$(printf '%s' "$raw" | sed 's/^0*//')"
  [ -n "$raw" ] || raw="0"
  printf '%s' "$raw"
}

now_epoch() {
  if [ -n "$RECORD_NOW_EPOCH" ]; then
    printf '%s' "$RECORD_NOW_EPOCH"
  else
    date -u +%s
  fi
}

# epoch -> UTC date (GNU first for CI, BSD fallback for macOS dev).
fmt_day() { # <epoch>
  local out
  out="$(date -u -d "@$1" +%Y-%m-%d 2>/dev/null || true)"
  if [ -z "$out" ]; then out="$(date -u -r "$1" +%Y-%m-%d 2>/dev/null || true)"; fi
  printf '%s' "$out"
}

# epoch -> ISO-8601 Z (same two-platform approach).
fmt_iso() { # <epoch>
  local out
  out="$(date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"
  if [ -z "$out" ]; then out="$(date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"; fi
  printf '%s' "$out"
}

# Fixed-point percentage, one decimal. `n/a` when the denominator is 0 — a day
# with no delivered samples has NO availability, and printing 0% or 100% there
# would be exactly the lie this instrument exists to prevent.
pct() { # <numerator> <denominator>
  local n d
  n="$(to_int "${1:-}" "0")"
  d="$(to_int "${2:-}" "0")"
  if [ "$d" -le 0 ]; then printf 'n/a'; return 0; fi
  awk -v n="$n" -v d="$d" 'BEGIN { printf "%.1f", (100.0 * (n / d)) }'
}

urlencode() { printf '%s' "$1" | jq -sRr @uri; }

# ── 1. enumerate the watchdog's runs in the window ──────────────────────────
# A run in the window IS a delivered sample. `created_at` decides its UTC day,
# `conclusion` decides whether it carried a verdict. Bounded pagination, same
# as the watchdog's searches: GitHub caps run listing at 1000 results, so
# `--paginate` cannot walk off the end.
fetch_runs() { # <since-day YYYY-MM-DD> -> TSV "day<TAB>conclusion" in $RUN_TMP
  local enc out
  enc="$(urlencode ">=${1}")"
  if ! out="$(gh api "repos/${REPO}/actions/workflows/${WATCHDOG_WORKFLOW}/runs?per_page=100&created=${enc}" --paginate 2>"$RUN_TMP/runs.err")"; then
    fail "could not list runs of ${WATCHDOG_WORKFLOW}: $(head -c 200 "$RUN_TMP/runs.err" 2>/dev/null || true)"
    return 1
  fi
  # `gh api --paginate` concatenates page objects (NOT an array of pages), so
  # slurp with jq -s and flatten `workflow_runs`. A run with a missing
  # created_at is DROPPED rather than bucketed into "today" — an unknown day is
  # not a sample.
  if ! printf '%s' "$out" | jq -rs '
      [ .[].workflow_runs[]? ]
      | map(select((.created_at // "") != ""))
      | .[]
      | [ (.created_at[0:10]), (.conclusion // "inconclusive") ]
      | @tsv' > "$RUN_TMP/runs.tsv" 2>"$RUN_TMP/runs.jq.err"; then
    fail "run list was not the expected JSON shape: $(head -c 200 "$RUN_TMP/runs.jq.err" 2>/dev/null || true)"
    return 1
  fi
  return 0
}

# ── 2. read the incident ledger ─────────────────────────────────────────────
# Search by the BODY marker + the reserved Actions login, then re-verify both
# client-side (mirroring the watchdog's own adoption guard — the repo is
# public, and a human-authored look-alike must never be read as machine state).
# Writes TSV: number, kind, first_failure_ts, restarts_raw
fetch_incidents() {
  local q enc out
  q="repo:${REPO} is:issue in:body author:app/github-actions \"${INCIDENT_STATE_MARKER}\""
  enc="$(urlencode "$q")"
  if ! out="$(gh api "search/issues?q=${enc}&per_page=100" --paginate 2>"$RUN_TMP/ledger.err")"; then
    fail "could not search incident ledger issues: $(head -c 200 "$RUN_TMP/ledger.err" 2>/dev/null || true)"
    return 1
  fi
  if ! printf '%s' "$out" | jq -rs --arg login "github-actions[bot]" --arg marker "$INCIDENT_STATE_MARKER" '
      def grab($re): try ((.body // "") | capture($re) | .v) catch "";
      [ .[].items[]? ]
      | map(select((.user.login // "") == $login))
      | map(select((.body // "") | contains($marker)))
      | .[]
      | (grab("watchdog-state[^>]*kind=(?<v>[a-zA-Z]+)")) as $k
      | (grab("watchdog-state[^>]*first_failure_ts=(?<v>[0-9]+)")) as $f
      | (grab("watchdog-state[^>]*restarts=(?<v>[^>]*)")) as $r
      | [ (.number | tostring),
          ($k | ascii_downcase),
          ($f // ""),
          (($r // "") | gsub("^[-\\s]+|[-\\s]+$"; "")) ]
      | @tsv' > "$RUN_TMP/incidents.raw.tsv" 2>"$RUN_TMP/ledger.jq.err"; then
    fail "incident ledger search was not the expected JSON shape: $(head -c 200 "$RUN_TMP/ledger.jq.err" 2>/dev/null || true)"
    return 1
  fi
  # Normalize the incident's first-observation EPOCH to a UTC day HERE, once, so
  # the per-day aggregation and the printed list can never disagree about which
  # day an incident belongs to (they did: bucketing compared a raw epoch against
  # a formatted day and every incident column silently read 0).
  : > "$RUN_TMP/incidents.tsv"
  local inc_n inc_kind inc_ts inc_rest inc_day
  while IFS="$(printf '\t')" read -r inc_n inc_kind inc_ts inc_rest; do
    [ -n "$inc_n" ] || continue
    inc_day="n/a"
    case "${inc_ts:-}" in
      ''|*[!0-9]*) : ;;
      *) inc_day="$(fmt_day "$inc_ts")" ;;
    esac
    printf '%s\t%s\t%s\t%s\n' "$inc_n" "$inc_kind" "$inc_day" "${inc_rest:-}" >> "$RUN_TMP/incidents.tsv"
  done < "$RUN_TMP/incidents.raw.tsv"
  return 0
}

# ── 3. render the record ────────────────────────────────────────────────────
# Reads $RUN_TMP/runs.tsv and $RUN_TMP/incidents.tsv. Prints the complete record
# on stdout. This is DATA (the script's output), and the harness asserts on it.
render_record() { # <days> <now>
  local days="$1" now="$2"
  local total_intended=$((days * CRON_PER_DAY))
  local total_delivered=0 total_failed=0 total_inconclusive=0
  local total_down_inc=0 total_degraded_inc=0 total_unknown_inc=0 total_restarts=0
  local zero_days=0 partial_days=0 gap_days_list=""
  local day_i day d_del d_fail d_inc pct_avail del_ok

  printf 'availability record — RECORD-ONLY baseline (#3810) — INSTRUMENT, NOT A GATE\n'
  printf 'generated: %s   window: %s UTC day(s) ending %s\n' "$(fmt_iso "$now")" "$days" "$(fmt_day "$now")"
  printf 'cron intent: %s samples/day (every 5 min). GitHub delays/drops scheduled runs, so delivered < intended is NORMAL — the cadence factor is the REAL resolution.\n' "$CRON_PER_DAY"
  printf 'failed_runs = runs whose probe verdict was non-zero (DOWN *or* DEGRADED). The incident columns keep the two kinds SEPARATE and are never summed.\n'
  printf '\n'

  # Per-day run counts, aggregated once. Every day in the window gets a row,
  # INCLUDING days with zero delivered samples: a gap must be distinguishable
  # from a down period, and an omitted row is exactly how a gap disappears.
  awk -F'\t' '{ d[$1]++; if ($2 == "failure") f[$1]++; else if ($2 != "success") o[$1]++ }
              END { for (k in d) printf "%s\t%d\t%d\t%d\n", k, d[k]+0, f[k]+0, o[k]+0 }' \
    "$RUN_TMP/runs.tsv" > "$RUN_TMP/day_runs.tsv" 2>/dev/null || : > "$RUN_TMP/day_runs.tsv"

  # Per-day incident counts (bucketed by first_failure_ts — the day the
  # incident was FIRST observed, i.e. the day whose availability it explains).
  awk -F'\t' '{ day = $3; if (day == "") next;
                if ($2 == "down") dn[day]++; else if ($2 == "degraded") dg[day]++; else un[day]++;
                n = split($4, a, ","); c = 0; for (i = 1; i <= n; i++) if (a[i] != "") c++;
                rs[day] += c; seen[day] = 1 }
              END { for (k in seen) printf "%s\t%d\t%d\t%d\t%d\n", k, dn[k]+0, dg[k]+0, un[k]+0, rs[k]+0 }' \
    "$RUN_TMP/incidents.tsv" > "$RUN_TMP/day_incidents.tsv" 2>/dev/null || : > "$RUN_TMP/day_incidents.tsv"

  # ── per-day table ──
  printf '%-12s %-9s %-9s %-9s %-9s %-10s %-56s %-9s %-13s %-12s %s\n' \
    'UTC day' 'delivered' 'intended' 'cadence' 'failed' 'inconcl.' 'sampled_availability (printed WITH its resolution)' 'down_inc' 'degraded_inc' 'unknown_inc' 'restarts'
  printf -- '-%.0s' $(seq 1 160); printf '\n'

  # Iterate the window day-by-day, oldest first.
  day_i="$((days - 1))"
  while [ "$day_i" -ge 0 ]; do
    day="$(fmt_day "$((now - day_i * 86400))")"
    local line inc_line inc_down inc_deg inc_unk d_rest
    line="$(awk -F'\t' -v d="$day" '$1 == d { print $2 "\t" $3 "\t" $4; found = 1 } END { if (!found) print "0\t0\t0" }' "$RUN_TMP/day_runs.tsv")"
    d_del="$(printf '%s' "$line" | cut -f1)"
    d_fail="$(printf '%s' "$line" | cut -f2)"
    d_inc="$(printf '%s' "$line" | cut -f3)"
    inc_line="$(awk -F'\t' -v d="$day" '$1 == d { print $2 "\t" $3 "\t" $4 "\t" $5; found = 1 } END { if (!found) print "0\t0\t0\t0" }' "$RUN_TMP/day_incidents.tsv")"
    inc_down="$(printf '%s' "$inc_line" | cut -f1)"
    inc_deg="$(printf '%s' "$inc_line" | cut -f2)"
    inc_unk="$(printf '%s' "$inc_line" | cut -f3)"
    d_rest="$(printf '%s' "$inc_line" | cut -f4)"

    total_delivered=$((total_delivered + d_del))
    total_failed=$((total_failed + d_fail))
    total_inconclusive=$((total_inconclusive + d_inc))
    total_down_inc=$((total_down_inc + inc_down))
    total_degraded_inc=$((total_degraded_inc + inc_deg))
    total_unknown_inc=$((total_unknown_inc + inc_unk))
    total_restarts=$((total_restarts + d_rest))

    if [ "$d_del" -eq 0 ]; then
      zero_days=$((zero_days + 1))
      gap_days_list="${gap_days_list}${gap_days_list:+, }${day}"
    elif [ "$d_del" -lt "$CRON_PER_DAY" ]; then
      partial_days=$((partial_days + 1))
    fi

    # THE LOAD-BEARING LINE: delivered / intended / cadence IMMEDIATELY beside
    # the availability figure. Removing any of them makes the harness red.
    if [ "$d_del" -eq 0 ]; then
      pct_avail="n/a (NO samples delivered — a gap is not an uptime sample; delivered=0/${CRON_PER_DAY} cadence=$(pct 0 "$CRON_PER_DAY")%)"
    else
      del_ok=$((d_del - d_fail))
      pct_avail="$(pct "$del_ok" "$d_del")% (delivered=${d_del}/${CRON_PER_DAY} cadence=$(pct "$d_del" "$CRON_PER_DAY")%)"
    fi
    printf '%-12s %-9s %-9s %-9s %-9s %-10s %-56s %-9s %-13s %-12s %s\n' \
      "$day" "$d_del" "$CRON_PER_DAY" "$(pct "$d_del" "$CRON_PER_DAY")%" "$d_fail" "$d_inc" "$pct_avail" "$inc_down" "$inc_deg" "$inc_unk" "$d_rest"
    day_i=$((day_i - 1))
  done

  printf -- '-%.0s' $(seq 1 160); printf '\n'
  printf '%-12s %-9s %-9s %-9s %-9s %-10s %-56s %-9s %-13s %-12s %s\n' \
    'TOTAL' "$total_delivered" "$total_intended" "$(pct "$total_delivered" "$total_intended")%" "$total_failed" "$total_inconclusive" \
    "$(pct "$((total_delivered - total_failed))" "$total_delivered")% (delivered=${total_delivered}/${total_intended} cadence=$(pct "$total_delivered" "$total_intended")%)" \
    "$total_down_inc" "$total_degraded_inc" "$total_unknown_inc" "$total_restarts"

  # ── gaps ──
  printf '\n'
  printf 'GAPS — intervals where the probe delivered FEWER samples than its cron intended.\n'
  printf 'A low cadence means the PROBE was not running; it does NOT mean the service was well.\n'
  printf 'Neither is visible in a single availability percentage, which is why it is never printed alone.\n'
  printf '  zero-delivery days: %s of %s' "$zero_days" "$days"
  if [ -n "$gap_days_list" ]; then printf ' (%s)' "$gap_days_list"; fi
  printf '\n'
  printf '  partial days (<%s delivered): %s of %s\n' "$CRON_PER_DAY" "$partial_days" "$days"
  printf '  window delivery: %s/%s samples (cadence %s%%) — the probe'"'"'s real resolution is %s%% of the nominal 5-minute cadence\n' \
    "$total_delivered" "$total_intended" "$(pct "$total_delivered" "$total_intended")" "$(pct "$total_delivered" "$total_intended")"
  printf '  inconclusive runs (not success, not failure — e.g. cancelled): %s\n' "$total_inconclusive"

  # ── incidents ──
  printf '\n'
  printf 'INCIDENTS — from ledger issues carrying %s (kind and restarts read from each state block).\n' "$INCIDENT_STATE_MARKER"
  printf 'DOWN (no answer) and DEGRADED (answered wrongly) are SEPARATE product failures and are never summed.\n'
  printf '  incident totals: down=%s degraded=%s unknown=%s restart_attempts=%s\n' \
    "$total_down_inc" "$total_degraded_inc" "$total_unknown_inc" "$total_restarts"
  if [ ! -s "$RUN_TMP/incidents.tsv" ]; then
    printf '  (none in the window)\n'
  else
    local inc_n inc_kind inc_day inc_rest rest_disp restart_count
    while IFS="$(printf '\t')" read -r inc_n inc_kind inc_day inc_rest; do
      [ -n "$inc_n" ] || continue
      [ -n "$inc_day" ] || inc_day="n/a"
      if [ -n "$inc_rest" ]; then rest_disp="$inc_rest"; else rest_disp="(none)"; fi
      restart_count="$(printf '%s' "$inc_rest" | tr ',' '\n' | grep -c '[0-9]' || true)"
      printf '  #%s  first_observed=%s  kind=%s  restarts=%s  restart_attempts=%s\n' \
        "$inc_n" "$inc_day" "$inc_kind" "$rest_disp" "$restart_count"
    done < "$RUN_TMP/incidents.tsv"
  fi

  printf '\n'
  printf 'RECORD-ONLY: no verdict, no target, no gate, no page, no restart. Publication of any figure\n'
  printf 'is an OWNER decision — this record is evidence, not an SLA.\n'
  printf '%s\n' "$RECORD_STATE_MARKER"
}

# ── 4. publish the record into ONE title-keyed issue ────────────────────────
# Mirrors the watchdog's dedupe APPROACH (title-keyed search + reserved-login +
# exact-title + body-marker re-check), not its code: the record issue is a
# long-lived rolling document, so it is always PATCHed in place and never
# closed. Search failure is __ERR__ (never "create a duplicate").
search_record_issue() {
  local q enc out n
  q="repo:${REPO} is:issue is:open in:title author:app/github-actions \"${RECORD_TITLE_MARKER}\""
  enc="$(urlencode "$q")"
  if ! out="$(gh api "search/issues?q=${enc}&per_page=100" --paginate 2>"$RUN_TMP/record.err")"; then
    fail "record-issue search failed: $(head -c 200 "$RUN_TMP/record.err" 2>/dev/null || true)"
    printf '__ERR__'
    return 0
  fi
  if ! n="$(printf '%s' "$out" | jq -rs --arg login "github-actions[bot]" --arg title "$RECORD_TITLE" --arg marker "$RECORD_STATE_MARKER" \
      '[.[].items[]? | select((.user.login // "") == $login) | select((.title // "") == $title) | select(((.body // "") | contains($marker)))][0].number // empty' 2>/dev/null)"; then
    fail "record-issue search returned an unparseable body"
    printf '__ERR__'
    return 0
  fi
  case "$n" in
    '') printf '' ;;
    *[!0-9]*|0) printf '__ERR__' ;;
    *) printf '%s' "$n" ;;
  esac
}

publish_record() { # <record-text> -> 0 ok / 1 failed (NEVER fails on the figures)
  local body="$1" n payload
  n="$(search_record_issue)"
  case "$n" in
    __ERR__) fail "refusing to publish: the record issue could not be searched (a duplicate is worse than a stale record)"; return 1 ;;
    '') : ;;
    *) payload="$(jq -n --arg b "$body" '{body:$b}')"
       if ! printf '%s' "$payload" | gh api "repos/${REPO}/issues/${n}" --method PATCH --input - >/dev/null 2>"$RUN_TMP/record-patch.err"; then
         fail "record issue #${n} update failed: $(head -c 200 "$RUN_TMP/record-patch.err" 2>/dev/null || true)"
         return 1
       fi
       note "updated the availability record in #${n}"
       return 0 ;;
  esac
  payload="$(jq -n --arg t "$RECORD_TITLE" --arg b "$body" '{title:$t, body:$b}')"
  if ! printf '%s' "$payload" | gh api "repos/${REPO}/issues" --method POST --input - >"$RUN_TMP/record-create.json" 2>"$RUN_TMP/record-create.err"; then
    fail "record issue create failed: $(head -c 200 "$RUN_TMP/record-create.err" 2>/dev/null || true)"
    return 1
  fi
  note "created the availability record issue #$(jq -r '.number // "?"' "$RUN_TMP/record-create.json" 2>/dev/null || printf '?')"
  return 0
}

# ── main ────────────────────────────────────────────────────────────────────
main() {
  local days="" print_only=0 arg="" now record rc=0
  for arg in "$@"; do
    case "$arg" in
      --print-only) print_only=1 ;;
      ''|*[!0-9]*) fail "unrecognised argument '${arg}' (expected a window in days, or --print-only)"; exit 2 ;;
      *) days="$arg" ;;
    esac
  done
  days="$(to_int "${days:-${AVAILABILITY_RECORD_DAYS:-28}}" "28")"
  [ "$days" -ge 1 ] 2>/dev/null || days=28
  [ "${AVAILABILITY_RECORD_PRINT_ONLY:-0}" = "1" ] && print_only=1

  if ! command -v jq >/dev/null 2>&1; then
    fail "jq is required"
    exit 1
  fi
  if [ -z "$REPO" ]; then
    fail "no repository: set GITHUB_REPOSITORY (or RECORD_REPO)"
    exit 1
  fi
  if [ "$print_only" != "1" ] && [ -z "${GH_TOKEN:-}" ]; then
    fail "GH_TOKEN is not set — cannot read runs or publish the record (use --print-only for a local render)"
    exit 1
  fi

  now="$(now_epoch)"
  local since_day
  since_day="$(fmt_day "$((now - (days - 1) * 86400))")"
  log "availability-record (#3810) — RECORD-ONLY baseline, window=${days}d since ${since_day}, workflow=${WATCHDOG_WORKFLOW}"

  fetch_runs "$since_day" || exit 1
  fetch_incidents || exit 1

  record="$(render_record "$days" "$now")"

  # The record is the script's OUTPUT — stdout is DATA here, and the harness
  # asserts on it. Logs stay on stderr.
  printf '%s\n' "$record"

  if [ "$print_only" = "1" ]; then
    log "print-only: not publishing"
    exit 0
  fi
  publish_record "$record" || rc=1
  # rc is 1 ONLY for an instrument failure (search/create/patch). It is NEVER
  # set from the measured figures — low availability is a reading, not an error.
  exit "$rc"
}

main "$@"
