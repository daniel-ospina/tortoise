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
# DOWNTIME IS A MEASUREMENT, NOT A RECOLLECTION (#3484). "How much downtime did
# that cost" was unanswerable without a human opening incidents one at a time:
# the ledger carried WHEN an incident opened, and the recovery existed only in
# the issue's `closed_at`, which nothing read. Every incident contributes the
# span first_seen_down -> closed.
#
# ⛔ MINUTES ARE WALL-CLOCK, NOT A SUM OF DURATIONS. Overlapping incidents are
# UNIONED, because one outage can open more than one issue and "minutes down" is
# elapsed time, not a tally of tickets. Minutes are attributed to the UTC day the
# minute OCCURRED. Incident COUNTS are attributed to the day an incident was
# FIRST observed — a count belongs to the incident, a minute belongs to the clock,
# and the legend says which column is which so no reader has to infer it.
# (Round-2 review P2: the first cut summed per-incident durations and overstated
# the 28-day window by ~475 min / 12.5% — the ledger holds 39 overlapping DOWN
# pairs, #13930/#13931, #14036/#14041, #14080/#14082, #14116/#14117 among them.)
#
# ⛔ THE SPAN IS SPLIT BY KIND (`down_min` / `degraded_min`), because DOWN and
# DEGRADED are different product failures and this file never sums them.
#
# TWO AGGREGATE CLASSES, and they are never collapsed:
#   counted    a DOWN or DEGRADED incident with a bounded span — the ONLY shape
#              that contributes minutes, and the only one in a minutes column
#   unbounded  everything else, counted in `unbounded_inc` and contributing NO
#              minutes: still open; closed with an unreadable recovery stamp;
#              closed as not_planned/duplicate (a human decision is not a
#              recovery); a recovery stamp EARLIER than the first failure; no
#              parseable first_failure_ts; or a usable span with no parseable
#              kind. Disclosing them together is what guarantees no incident can
#              be simultaneously INVISIBLE and costed at 0 minutes — but each
#              carries its own label in the per-incident list, so "we do not know
#              when it ended" is never printed as "it is down right now".
# The span's resolution is the probe's real cadence (printed above it): an
# outage shorter than one probe interval cannot be told apart from one interval.
#
# A FOURTH INVARIANT (#3896): the window is reported ONLY if the run fetch was
# PROVEN COMPLETE. GitHub's runs endpoint returns at most 1,000 results for a
# `created`-filtered query, so the fetch walks one bounded UTC-day slice at a
# time and checks each slice's enumeration against the API's own total_count. A
# truncated or failed fetch is an INSTRUMENT FAILURE: it explains itself on
# stderr, exits non-zero and publishes NOTHING. It is never rendered as "NO
# samples delivered" — a day the fetch never reached is not a day the probe did
# not run, and a record that lies is worse than no record at all. A returned run
# that cannot be PLACED on a UTC day (no `created_at`) is the same class:
# dropping it would shrink the denominator the published figure is computed
# over — OVERSTATING availability when the dropped run was a failure — while the
# marker still certified the pre-drop count. It fails the fetch closed, never
# quietly excluded.
#
# A fifth: a run that carried NO verdict (cancelled / timed_out / in-progress /
# skipped) is not evidence of availability AND not evidence of failure. It is
# excluded from both the numerator and the denominator of availability, counted
# in its own column, and its denominator (decided=K/D) is printed beside every
# figure. A percentage over a denominator that includes non-observations is a
# percentage that a cancellation can inflate.
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
# Harness: .github/scripts/availability-record.test.sh (stubs `gh`, no network, the
# `Availability record-only baseline (#3810)` step of ci.yml's `ops-harnesses` job).
# Run: bash .github/scripts/availability-record.test.sh
# ============================================================================
set -uo pipefail

# ── config ──────────────────────────────────────────────────────────────────
REPO="${RECORD_REPO:-${GITHUB_REPOSITORY:-}}"
WATCHDOG_WORKFLOW="${RECORD_WORKFLOW:-availability-watchdog.yml}"
# The cron intent: every 5 minutes = 288/day. This is the DENOMINATOR of the
# cadence factor, i.e. what the probe was supposed to deliver. Normalized in
# main() through to_int like every other numeric input: it used to reach
# `$((days * CRON_PER_DAY))` raw, where a non-numeric value is read as a
# VARIABLE name and `set -u` aborts the whole run with a bare "unbound
# variable" instead of recording anything (#3896 P2-2).
CRON_PER_DAY="${RECORD_CRON_PER_DAY:-288}"
# GitHub's documented maximum number of results returned by the runs endpoint
# for a `created`-filtered (or actor/branch/event/head_sha/status-filtered)
# query. It bounds the ENUMERATION, not the search: the tail of the window is
# discarded, which is the defect this file's chunked fetch exists to defeat
# (#3896). See fetch_runs for how each slice is proven to be under it.
RUN_FETCH_CAP=1000
# Test seam for a deterministic "now".
RECORD_NOW_EPOCH="${RECORD_NOW_EPOCH:-}"
# Test seam: override the fetch-integrity marker render_record reads, so the
# refuse-to-render guard is covered by the harness instead of being dead code.
RECORD_FETCH_INTEGRITY="${RECORD_FETCH_INTEGRITY:-}"

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

# ISO-8601 Z -> epoch (the INVERSE of fmt_iso, needed for the ledger's
# `closed_at`, which the API hands back as a string). Prints NOTHING when the
# value is not a renderable timestamp: the caller treats "" as UNKNOWN rather
# than as 0, because epoch 0 would read as a 56-year outage.
iso_to_epoch() { # <ISO-8601> -> epoch or empty
  local iso="${1:-}" out digits
  case "$iso" in
    ????-??-??T??????*) : ;;
    *) printf ''; return 0 ;;
  esac
  out="$(date -u -d "$iso" +%s 2>/dev/null || true)"
  if [ -z "$out" ]; then
    out="$(date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$iso" +%s 2>/dev/null || true)"
  fi
  # Accept only plain digits of a plausible epoch width. Anything else is an
  # unparseable timestamp, and no caller may do arithmetic on it.
  case "$out" in
    ''|*[!0-9]*) printf '' ;;
    *) digits="${#out}"; if [ "$digits" -ge 9 ] && [ "$digits" -le 12 ]; then printf '%s' "$out"; fi ;;
  esac
}

# epoch -> ISO-8601 Z (same two-platform approach).
fmt_iso() { # <epoch>
  local out
  out="$(date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"
  if [ -z "$out" ]; then out="$(date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)"; fi
  printf '%s' "$out"
}

# The window as an explicit list of UTC days, OLDEST FIRST. Written ONCE by main
# and read by BOTH fetch_runs and render_record, so the set of days that is
# fetched and the set of rows that is rendered cannot drift apart — a drift
# would be a silent truncation of a different shape (fetched days with no row,
# or rows with no fetch).
window_days() { # <days> <now> -> one YYYY-MM-DD per line
  # NB: fmt_day prints WITHOUT a newline (it is a command-substitution helper),
  # so each day is terminated explicitly — an unterminated final line makes
  # `read` return EOF and the whole list is silently dropped.
  local days="$1" now="$2" i
  for ((i = days - 1; i >= 0; i--)); do printf '%s\n' "$(fmt_day "$((now - i * 86400))")"; done
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
# `conclusion` decides whether it carried a verdict.
#
# ⛔ WHY THIS IS CHUNKED ONE UTC DAY AT A TIME (#3896)
# The runs endpoint "will return up to 1,000 results for each search when using
# the following parameters: actor, branch, check_suite_id, created, event,
# head_sha, status" (docs.github.com/en/rest/actions/workflow-runs). The cap
# therefore does NOT bound the search — a single `created=>=DAY` query silently
# DISCARDS THE TAIL of the window. At the probe's measured ~96 runs/day (#3810)
# a 28-day window is ~2,700 runs, so only the newest ~10 days came back and the
# older ~18 were bucketed as zero-delivery, rendered "NO samples delivered",
# i.e. a fetch limit printed in the instrument's own voice as "the PROBE was
# not running". Verified against this repo: python-ci.yml enumerates exactly
# 1,000 runs under a `created` filter while its own total_count for the same
# query is 5,644.
#
# The fix: one BOUNDED query per UTC day —
# `created=DAYT00:00:00Z..DAYT23:59:59Z` — concatenated. A UTC day can hold at
# most the cron's own 288 intended intervals (measured: ~96), i.e. >=3.4x of
# headroom under the cap, and costs one request in the common case. No wider
# slice is structurally safe: 4 days is 1,152 at nominal cadence (already over
# the cap), 7 days is ~2,016. The bounds are inclusive at second resolution, so
# adjacent slices neither overlap nor leave a gap.
#
# Completeness is checked with TWO INDEPENDENT COUNTS per slice:
#   * `total_count` — the API's own count of runs matching that filter. It is a
#     COUNT, not the enumeration, and is NOT clipped at the cap (python-ci:
#     5,644 reported against 1,000 enumerated). Proven truthful for a bounded
#     slice: the watchdog's 2026-09-16 slice reports 97, and 97 runs enumerate.
#   * the number of runs the slice actually returned.
# They must agree, and the reported total must be under the cap. If not, the
# fetch CANNOT BE TRUSTED: it is an INSTRUMENT FAILURE — why, on stderr; a
# non-zero exit; and NOTHING published. It is never rendered as a zero-delivery
# day. The verdict is recorded in $RUN_TMP/fetch_integrity.txt and
# render_record refuses to print a record unless it is COMPLETE.
fetch_runs() { # reads $RUN_TMP/window_days.txt -> TSV "day<TAB>conclusion" in $RUN_TMP
  local day enc out
  local slice_returned slice_total slice_lines slice_pages
  local all_returned=0 all_reported=0 slices=0
  : > "$RUN_TMP/runs.tsv"
  # Fail-closed default: anything that returns without overwriting this marker
  # (including a future edit that deletes the checks) leaves the render gated.
  printf 'TRUNCATED\t0\t0\t0\t%s\n' "$RUN_FETCH_CAP" > "$RUN_TMP/fetch_integrity.txt"

  while IFS= read -r day; do
    [ -n "$day" ] || continue
    enc="$(urlencode "${day}T00:00:00Z..${day}T23:59:59Z")"
    if ! out="$(gh api "repos/${REPO}/actions/workflows/${WATCHDOG_WORKFLOW}/runs?per_page=100&created=${enc}" --paginate 2>"$RUN_TMP/runs.err")"; then
      fail "could not list runs of ${WATCHDOG_WORKFLOW} for the ${day} slice: $(head -c 200 "$RUN_TMP/runs.err" 2>/dev/null || true)"
      return 1
    fi
    # `gh api --paginate` concatenates page objects (NOT an array of pages), so
    # slurp with jq -s and flatten `workflow_runs`.
    slice_returned="$(printf '%s' "$out" | jq -rs '[ .[].workflow_runs[]? ] | length' 2>/dev/null || printf 'x')"
    slice_total="$(printf '%s' "$out" | jq -rs '[ .[].total_count? // 0 ] | max // 0' 2>/dev/null || printf 'x')"
    # How many pages actually carried a numeric total_count. An empty body, or
    # any JSON with no page object, parses "successfully" to zero runs — and a
    # zero-run slice is indistinguishable from a day the probe did not run. The
    # API always sends total_count, so a response without it is not a real
    # answer and must not be read as an empty window.
    slice_pages="$(printf '%s' "$out" | jq -rs '[ .[] | select((.total_count? | type) == "number") ] | length' 2>/dev/null || printf 'x')"
    case "${slice_returned}${slice_total}${slice_pages}" in
      ''|*[!0-9]*)
        fail "run list for the ${day} slice was not the expected JSON shape (no workflow_runs/total_count)"
        return 1 ;;
    esac
    # ── no page object at all: an unparseable/empty answer, NOT an empty day ──
    if [ "$slice_pages" -lt 1 ]; then
      fail "INSTRUMENT FAILURE — the ${day} slice returned no page object carrying total_count (an empty or unexpected response). Zero runs from an ANSWER we cannot parse is not a day the probe did not run. No record is published."
      printf 'TRUNCATED: slice %s returned no page with total_count\n' "$day" > "$RUN_TMP/fetch_integrity.txt"
      return 1
    fi
    # ── the slice is at or over the cap: it cannot be told apart from a clipped one ──
    # `-ge`, not `-gt`: at EXACTLY the cap the two counts still agree, so a
    # day GitHub had itself clipped to 1,000 would pass every other check and
    # its tail would vanish into "NO samples delivered". A UTC day reaching
    # 1,000 runs is 3.5x the cron's own nominal 288, so refusing the boundary
    # costs nothing real and is the only fail-closed reading.
    if [ "$slice_total" -ge "$RUN_FETCH_CAP" ]; then
      fail "INSTRUMENT FAILURE — run fetch TRUNCATED at GitHub's ${RUN_FETCH_CAP}-result cap: the ${day} slice reports total_count=${slice_total}. A slice AT or OVER the cap cannot be told apart from a clipped one, so this window CANNOT be trusted. This is a FETCH limit, NOT evidence that the probe was not running. No record is published."
      printf 'TRUNCATED: slice %s reported total_count=%s > cap %s\n' "$day" "$slice_total" "$RUN_FETCH_CAP" > "$RUN_TMP/fetch_integrity.txt"
      return 1
    fi
    # ── the two independent counts disagree: the tail was clipped ──
    if [ "$slice_returned" -ne "$slice_total" ]; then
      fail "INSTRUMENT FAILURE — run fetch TRUNCATED: the ${day} slice reports total_count=${slice_total} but only ${slice_returned} run(s) were returned (the ${RUN_FETCH_CAP}-result cap, or a lost page). Days the fetch never reached would render as \"NO samples delivered\", i.e. as the probe not running. This is a FETCH failure, NOT a gap. No record is published."
      printf 'TRUNCATED: slice %s returned %s of %s reported\n' "$day" "$slice_returned" "$slice_total" > "$RUN_TMP/fetch_integrity.txt"
      return 1
    fi
    # A run whose created_at cannot be placed on a UTC day cannot be PLACED at
    # all. That covers BOTH a missing created_at AND one that is present but is
    # not a `YYYY-MM-DD` UTC date — whitespace, "not-a-date", or any other shape
    # whose first 10 characters are not a date. The old filter tested only
    # `!= ""`, so `created_at: " "` and `created_at: "not-a-date"` sailed
    # through, were bucketed under a day this window never renders, and were
    # DROPPED from the body while the marker still certified the pre-drop count
    # (#3810 P2-1). The run is not bucketed into "today" (an unknown day is not a
    # sample) and is not silently excluded either: dropping it would shrink the
    # denominator the published availability figure is computed over —
    # OVERSTATING availability when the dropped run was a failure. It is
    # therefore an INSTRUMENT FAILURE, exactly like every other run this fetch
    # cannot fully account for: why, on stderr; non-zero exit; NOTHING published.
    # (The real API always sends a well-formed created_at, so this is a
    # defensive path, not a live one.)
    if ! printf '%s' "$out" | jq -rs '
        [ .[].workflow_runs[]? ]
        | map(select((.created_at // "") | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}")))
        | .[]
        | [ (.created_at[0:10]), (.conclusion // "inconclusive") ]
        | @tsv' > "$RUN_TMP/slice.tsv" 2>"$RUN_TMP/runs.jq.err"; then
      fail "run list for ${day} was not the expected JSON shape: $(head -c 200 "$RUN_TMP/runs.jq.err" 2>/dev/null || true)"
      return 1
    fi
    slice_lines="$(awk 'END { print NR + 0 }' "$RUN_TMP/slice.tsv")"
    if [ "$slice_lines" -lt "$slice_returned" ]; then
      fail "INSTRUMENT FAILURE — the ${day} slice returned $((slice_returned - slice_lines)) run(s) with no created_at that is a usable UTC date (YYYY-MM-DD), so their UTC day cannot be determined. Dropping them would shrink the denominator the published figure is computed over and would OVERSTATE availability if any was a failure. This window CANNOT be trusted and no record is published. This is a FETCH failure, NOT a gap."
      printf 'INCOMPLETE: slice %s dropped %s of %s run(s) with no created_at that is a usable UTC date\n' "$day" "$((slice_returned - slice_lines))" "$slice_returned" > "$RUN_TMP/fetch_integrity.txt"
      return 1
    fi
    cat "$RUN_TMP/slice.tsv" >> "$RUN_TMP/runs.tsv"
    all_returned=$((all_returned + slice_returned))
    all_reported=$((all_reported + slice_total))
    slices=$((slices + 1))
  done < "$RUN_TMP/window_days.txt"

  if [ "$slices" -le 0 ]; then
    fail "INSTRUMENT FAILURE — the window contains no UTC days to fetch"
    return 1
  fi
  if [ "$all_returned" -ne "$all_reported" ]; then
    fail "INSTRUMENT FAILURE — run fetch TRUNCATED across the window: ${all_returned} run(s) returned, ${all_reported} reported. No record is published."
    printf 'TRUNCATED: window returned %s of %s reported\n' "$all_returned" "$all_reported" > "$RUN_TMP/fetch_integrity.txt"
    return 1
  fi
  printf 'COMPLETE\t%s\t%s\t%s\t%s\n' "$all_returned" "$all_reported" "$slices" "$RUN_FETCH_CAP" > "$RUN_TMP/fetch_integrity.txt"
  log "run fetch complete: ${all_returned} run(s) over ${slices} bounded UTC-day slice(s); the API reported ${all_reported} for the same slices; no slice reached the ${RUN_FETCH_CAP} cap"
  return 0
}

# ── 2. read the incident ledger ─────────────────────────────────────────────
# Search by the BODY marker + the reserved Actions login, then re-verify both
# client-side (mirroring the watchdog's own adoption guard — the repo is
# public, and a human-authored look-alike must never be read as machine state).
# Writes TSV: number, kind, first_failure_ts, closed_at, state_reason, state, restarts_raw
#
# WHY `closed_at` (#3484) — downtime was the one bucket figure nobody could
# answer: the ledger records WHEN an incident opened (`first_failure_ts`), and
# the recovery time lived only in the issue's own `closed_at`, which no consumer
# read. "How much downtime did that cost" therefore needed a human opening
# incidents one at a time. The span first-seen-down -> closed is the outage the
# probe OBSERVED; it is reported with its resolution (the probe's real cadence)
# and never summed with still-open incidents, whose outage is not yet bounded.
#
# `state_reason` is read for one reason: a human CLOSING an issue as
# `not_planned` or `duplicate` is NOT a recovery, and its `closed_at` would
# otherwise manufacture an outage span that never happened.
fetch_incidents() {
  local q enc out
  q="repo:${REPO} is:issue in:body author:app/github-actions \"${INCIDENT_STATE_MARKER}\""
  enc="$(urlencode "$q")"
  if ! out="$(gh api "search/issues?q=${enc}&per_page=100" --paginate 2>"$RUN_TMP/ledger.err")"; then
    fail "could not search incident ledger issues: $(head -c 200 "$RUN_TMP/ledger.err" 2>/dev/null || true)"
    return 1
  fi
  if ! printf '%s' "$out" | jq -rs --arg login "github-actions[bot]" --arg marker "$INCIDENT_STATE_MARKER" '
      # `capture` emits `empty` (NOT an error) when the regex does not match, so
      # `try (... | capture($re) | .v) catch ""` let an unmatched field ABANDON
      # the whole item — the incident vanished from the list AND the totals with
      # no warning (#3896 P2-3). Test first, capture second, and return the
      # ABSENT sentinel `-` so the field never goes missing.
      def grab($re): ((.body // "") as $b | if ($b | test($re)) then ($b | capture($re) | .v) else "-" end);
      [ .[].items[]? ]
      | map(select((.user.login // "") == $login))
      | map(select((.body // "") | contains($marker)))
      | .[]
      | (grab("watchdog-state[^>]*kind=(?<v>[a-zA-Z]+)")) as $k
      | (grab("watchdog-state[^>]*first_failure_ts=(?<v>[0-9]+)")) as $f
      | (grab("watchdog-state[^>]*restarts=(?<v>[^>]*)")) as $r
      | ((.closed_at // "") | tostring) as $c
      | ((.state_reason // "") | tostring) as $y
      | ((.state // "") | tostring) as $s
      | [ (.number | tostring),
          (($k // "-") | ascii_downcase),
          ($f // "-"),
          (if $c == "" then "-" else $c end),
          (if $y == "" then "-" else $y end),
          # `state` is what separates "still down" from "closed, but the recovery stamp is
          # unreadable". Without it the printer could only guess, and the guess it made was
          # the worst one available: asserting, for a CLOSED incident, that the service is
          # down right now with an elapsed time that grows for ever (review P2).
          (if $s == "" then "-" else $s end),
          # `-` is the sentinel; a PRESENT-but-empty restarts= also normalizes to
          # "". Both reach bash as `-`/"" in the LAST field, which is where an
          # empty value is safe: bash `read` with IFS=<tab> collapses an empty
          # MIDDLE field and shifts the remaining fields (#3896 P2-3).
          (($r // "-") | if . == "-" then "-" else gsub("^[-\\s]+|[-\\s]+$"; "") end)
        ]
      | @tsv' > "$RUN_TMP/incidents.raw.tsv" 2>"$RUN_TMP/ledger.jq.err"; then
    fail "incident ledger search was not the expected JSON shape: $(head -c 200 "$RUN_TMP/ledger.jq.err" 2>/dev/null || true)"
    return 1
  fi
  # Normalize the incident's first-observation EPOCH to a UTC day HERE, once, so
  # the per-day aggregation and the printed list can never disagree about which
  # day an incident belongs to (they did: bucketing compared a raw epoch against
  # a formatted day and every incident column silently read 0).
  : > "$RUN_TMP/incidents.tsv"
  local inc_n inc_kind inc_ts inc_closed inc_reason inc_state inc_rest inc_day
  local inc_ff inc_end
  while IFS="$(printf '\t')" read -r inc_n inc_kind inc_ts inc_closed inc_reason inc_state inc_rest; do
    [ -n "$inc_n" ] || continue
    [ "$inc_kind" = "-" ] && inc_kind="unknown"
    [ "$inc_rest" = "-" ] && inc_rest=""
    # ⛔ `state` KEEPS its `-` sentinel: an EMPTY MIDDLE field is collapsed by `read`, which
    # would shift `restarts` into the wrong column — the `#3896` P2-3 trap, one field over.
    [ -n "${inc_state:-}" ] || inc_state="-"
    inc_day="n/a"
    inc_ff="-"
    # ⛔ A WIDTH GATE, NOT JUST A DIGIT TEST (round-2 review P2). `08` passes a
    # `not-all-digits` test and is not a timestamp: GNU `date -d @08` renders
    # 1970-01-01, so the incident would claim a 56-year elapsed bound. `to_int` says
    # the same thing about this class (`:171`), and `iso_to_epoch` already applies a
    # 9..12-digit width gate to the OTHER end of the span. Real ledger stamps are 10
    # digits; anything outside 9..12 is not a renderable epoch and is treated as the
    # unparseable stamp it is (warned, listed, never counted).
    case "${inc_ts:-}" in
      ''|-|*[!0-9]*) : ;;
      *)
        if [ "${#inc_ts}" -ge 9 ] && [ "${#inc_ts}" -le 12 ]; then
          # ⛔ NORMALIZE AT INGEST, not at each consumer (rounds 4-6). A leading zero is
          # bash OCTAL: `01789547000` ABORTS the expansion (= the whole render dies and
          # publishes nothing), and a valid-octal `012345670` silently walks to a 1970
          # day. `awk` and `[ -ge ]` read the SAME string as DECIMAL, so two consumers
          # disagreed about one value while both looked right in isolation. `to_int` is
          # this file's normalizer (:186); an all-zero result is not a timestamp either
          # (epoch 0 would read as a 56-year incident), so it joins the unparseable class.
          inc_ff="$(to_int "$inc_ts" -)"
          case "$inc_ff" in
            ''|-|0) inc_ff="-" ;;
            *) inc_day="$(fmt_day "$inc_ff")" ;;
          esac
        fi
        ;;
    esac
    # A numeric epoch that `date` cannot render (out of range) must still land
    # as a DATED-NOTHING row, never as an empty field: bash `read` collapses an
    # empty MIDDLE field, which shifted the restart ledger into the day column
    # and printed a restart stamp as the observation day.
    [ -n "$inc_day" ] || inc_day="n/a"
    # The recovery end of the span. Three DISTINGUISHABLE states, never two:
    #   <epoch>  closed, therefore RECOVERED — a real, bounded outage
    #   -        never closed (still down, or closed with no readable stamp):
    #            the outage is NOT bounded, so it contributes no minutes
    #   x        closed as not_planned/duplicate by a human — NOT a recovery,
    #            excluded from every downtime figure rather than read as one
    inc_end="-"
    case "${inc_reason:-}" in
      not_planned|duplicate) inc_end="x" ;;
      *)
        case "${inc_closed:-}" in
          ''|-|x) : ;;
          *)
            inc_end="$(iso_to_epoch "$inc_closed")"
            [ -n "$inc_end" ] || inc_end="-"
            ;;
        esac
        ;;
    esac
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$inc_n" "$inc_kind" "$inc_day" "$inc_ff" "$inc_end" "${inc_state}" "${inc_rest:-}" >> "$RUN_TMP/incidents.tsv"
  done < "$RUN_TMP/incidents.raw.tsv"
  return 0
}

# ── 2b. wall-clock downtime per UTC day, by UNION of intervals (#3484) ─────
# ⛔ SPANS ARE NOT ADDITIVE (round-2 review P2). The ledger contains overlapping
# incidents — two probes can open two `kind=down` issues for one outage, and 39 such
# overlapping DOWN pairs exist in the live ledger (#13930/#13931, #14036/#14041,
# #14080/#14082, #14116/#14117) — so summing per-incident durations reports a
# number that never existed as elapsed time (measured: +475 min, ~12.5%, on the
# 28-day window). "Minutes the service was down" is a UNION of intervals.
#
# Each span is also CLIPPED to the UTC day it falls in, so a minute is counted on
# the day it actually happened. That is deliberately different from the incident
# COUNTS above, which stay attributed to the day an incident was first observed:
# a count belongs to the incident, a minute belongs to the clock. Both rules are
# stated in the rendered legend, because a reader must not have to infer which
# column uses which.
#
# Interval arithmetic is done on INTEGER epoch seconds in awk, never through
# `date`: an unrenderable stamp must not be able to abort the render, and this
# pass only ever runs on stamps that already passed the numeric + ordering gates.
build_span_union() { # reads $RUN_TMP/incidents.tsv -> $RUN_TMP/day_minutes.tsv
  : > "$RUN_TMP/spans.tsv"
  local inc_n inc_kind inc_day inc_ff inc_end inc_state inc_rest
  local dse day_s day_e guard
  while IFS="$(printf '\t')" read -r inc_n inc_kind inc_day inc_ff inc_end inc_state inc_rest; do
    [ -n "$inc_n" ] || continue
    [ "$inc_kind" = "down" ] || [ "$inc_kind" = "degraded" ] || continue
    case "${inc_ff:-}" in ''|-|*[!0-9]*) continue ;; esac
    case "${inc_end:-}" in ''|-|x|*[!0-9]*) continue ;; esac
    [ "$inc_end" -ge "$inc_ff" ] || continue
    # First UTC midnight at or before the span start, then walk forward. The guard
    # bounds the walk so a nonsense span cannot spin; a span beyond it is not a
    # measurement anyone should be printing anyway.
    # ⛔ THE DAY IS A HALF-OPEN INTERVAL [dse, dse+86400) (round 6). The first cut used the
    # INCLUSIVE last second (`dse + 86399`) and then divided the clipped length by 60, so
    # a fully covered UTC day measured 1439 min and EVERY midnight crossing lost a minute
    # at the boundary (23:00 -> 01:00 read 59 + 60 = 119 of 120). A minute is a DURATION:
    # the length of a clipped interval is the difference of its ends.
    dse=$(( inc_ff - (inc_ff % 86400) ))
    guard=0
    while [ "$dse" -lt "$inc_end" ] && [ "$guard" -lt 400 ]; do
      day_s=$(( inc_ff > dse ? inc_ff : dse ))
      day_e=$(( dse + 86400 ))
      [ "$inc_end" -lt "$day_e" ] && day_e="$inc_end"
      if [ "$day_e" -gt "$day_s" ]; then
        printf '%s\t%s\t%s\t%s\n' "$(fmt_day "$dse")" "$inc_kind" "$day_s" "$day_e" >> "$RUN_TMP/spans.tsv"
      fi
      dse=$(( dse + 86400 ))
      guard=$(( guard + 1 ))
    done
  done < "$RUN_TMP/incidents.tsv"

  # Union: sort by (day, kind, start) and merge every interval that starts at or
  # before the running end. Truncation to whole minutes happens ONCE, on the
  # merged interval — truncating per input interval would lose a minute per
  # overlap and make the union depend on how many issues one outage opened.
  # ⛔ A FAILED PASS IS NOT ZERO MINUTES (rounds 3, 5, 6). The first cut ended this
  # pipeline with `|| : > file`, so ANY failure in the chain — OOM, a full /tmp, a lost
  # pipe, or the render aborting above — truncated the minutes file, every day resolved
  # through the `if (!found) print 0` default, and the record published
  # `0 min DOWN over 1 DOWN incident(s)` directly beside `down_for=60 min`. The return
  # code is the contract now: the caller REFUSES to render, because an unusable
  # measurement must be DISCLOSED, never costed at 0 (like every fetch-side failure here).
  : > "$RUN_TMP/day_minutes.tsv"
  if [ -s "$RUN_TMP/spans.tsv" ]; then
    LC_ALL=C sort -t"$(printf '\t')" -k1,1 -k2,2 -k3,3n "$RUN_TMP/spans.tsv" \
      | awk -F'\t' '
        function flush() { if (have) printf "%s\t%s\t%d\n", cd, ck, int((ce - cs) / 60) }
        $1 != cd || $2 != ck { flush(); cd = $1; ck = $2; cs = $3; ce = $4; have = 1; next }
        { if ($3 <= ce) { if ($4 > ce) ce = $4 } else { flush(); cs = $3; ce = $4 } }
        END { flush() }' \
      | awk -F'\t' '{ key = $1 SUBSEP $2; m[key] += $3; if (!(key in seen)) { order[++n] = key; seen[key] = 1 } }
                     END { for (i = 1; i <= n; i++) { split(order[i], p, SUBSEP); printf "%s\t%s\t%d\n", p[1], p[2], m[order[i]] } }' \
      > "$RUN_TMP/day_minutes.tsv" || return 1
  fi
  # Test seam: force the pass to report failure, so the caller's refuse-to-render guard
  # is covered by the harness instead of being dead code (mirrors RECORD_FETCH_INTEGRITY).
  [ "${RECORD_UNION_FAIL:-0}" = "1" ] && return 1
  return 0
}

# ── 3. render the record ────────────────────────────────────────────────────
# Reads $RUN_TMP/runs.tsv, $RUN_TMP/incidents.tsv and
# $RUN_TMP/fetch_integrity.txt. Prints the complete record on stdout. This is
# DATA (the script's output), and the harness asserts on it.
#
# ⛔ IT REFUSES TO RENDER AN UNVERIFIED FETCH (#3896). The days a clipped fetch
# never reached are exactly the days that would print "NO samples delivered" —
# a claim that the PROBE was not running. That claim would be false, and a
# record that lies is worse than no record. The gate sits HERE, at the point the
# lie would be printed, and it re-derives the invariant from the marker rather
# than trusting it: the marker must say COMPLETE, its own two counts must agree
# (enumerated == reported by the API), and it must cover EVERY day of the
# window. A truncation that slipped past fetch_runs' checks is still refused.
render_record() { # <days> <now>
  local days="$1" now="$2"

  # ── fetch-completeness gate ──
  # The marker IS the trust anchor for the whole window, so it is parsed
  # strictly: a marker that is present but whose counts are missing or
  # non-numeric is REFUSED, never coerced to 0 (coercion would read a malformed
  # anchor as "zero runs on every slice" — i.e. as a complete empty window,
  # which is the same lie in a different costume).
  local integrity="" i_status="" i_enum="" i_rep="" i_slices="" i_cap=""
  local marker_malformed=0 v
  if [ -n "${RECORD_FETCH_INTEGRITY:-}" ]; then
    integrity="$RECORD_FETCH_INTEGRITY"
  elif [ -s "$RUN_TMP/fetch_integrity.txt" ]; then
    integrity="$(cat "$RUN_TMP/fetch_integrity.txt")"
  fi
  if [ -n "$integrity" ]; then
    # Field count FIRST: bash `read` with IFS=<tab> treats tab as IFS WHITESPACE,
    # so a run of tabs collapses and an EMPTY INTERIOR field silently shifts
    # every later value into the wrong slot — a 6-field marker with one gap
    # would otherwise look like a well-formed 5-field one. `awk` counts empty
    # fields; `read` does not.
    [ "$(printf '%s' "$integrity" | awk -F'\t' 'NR == 1 { print NF; exit }')" = "5" ] || marker_malformed=1
    IFS="$(printf '\t')" read -r i_status i_enum i_rep i_slices i_cap <<<"$integrity" || true
    [ -n "$i_status" ] || marker_malformed=1
    for v in "$i_enum" "$i_rep" "$i_slices" "$i_cap"; do
      case "$v" in ''|*[!0-9]*) marker_malformed=1 ;; esac
    done
  else
    i_status="no fetch-integrity marker was written"
    marker_malformed=1
  fi
  case "${i_enum:-}" in ''|*[!0-9]*) i_enum=0 ;; esac
  case "${i_rep:-}" in ''|*[!0-9]*) i_rep=0 ;; esac
  case "${i_slices:-}" in ''|*[!0-9]*) i_slices=0 ;; esac
  [ -n "${i_cap:-}" ] || i_cap="$RUN_FETCH_CAP"
  # The window's day count, so the marker can be held to covering all of it.
  local window_day_count window_enum refusal=""
  window_day_count="$(awk 'END { print NR + 0 }' "$RUN_TMP/window_days.txt" 2>/dev/null || printf 0)"
  # ── every enumerated run must land on a day this render SUMS (#3810 P2-1) ──
  # fetch_runs proves each slice's enumeration matches the API's own count and
  # that every returned run carries a placeable UTC day. It does NOT prove the
  # day is one this render will SUM: the per-day table iterates window_days.txt,
  # so a run whose (valid) created_at falls OUTSIDE the window is counted by the
  # marker — "COMPLETE — N enumerated" — and then has NO row. The body would
  # print zero delivery for every day while the marker certified the pre-drop
  # count: a false-absence claim over a dropped FAILURE run, the SAME class as a
  # missing created_at (#3896), one stage later. Reconcile the marker's
  # enumeration against the runs that actually land on a rendered day, and refuse
  # when they disagree, like every other instrument failure.
  window_enum="$(awk -F'\t' 'NR == FNR { w[$1] = 1; next } w[$1] { n++ } END { print n + 0 }' \
    "$RUN_TMP/window_days.txt" "$RUN_TMP/runs.tsv" 2>/dev/null || printf 0)"
  if [ "$marker_malformed" = "1" ]; then
    refusal="the fetch-integrity marker is missing or malformed (expected COMPLETE<TAB>enumerated<TAB>reported<TAB>slices<TAB>cap, all numeric): '${integrity:-<none>}'"
  elif [ "$i_status" != "COMPLETE" ]; then
    refusal="the fetch-integrity marker says: $i_status"
  elif [ "$i_enum" -ne "$i_rep" ]; then
    refusal="the fetch-integrity marker is internally inconsistent: $i_enum run(s) enumerated but $i_rep reported by the API"
  elif [ "$i_slices" -ne "$window_day_count" ]; then
    refusal="the fetch-integrity marker covers $i_slices slice(s) but the window has $window_day_count UTC day(s)"
  elif [ "$window_enum" -ne "$i_enum" ]; then
    refusal="the fetch enumerated $i_enum run(s) but only $window_enum fall on a UTC day this window renders — publishing would drop $((i_enum - window_enum)) enumerated run(s) from the body while the marker certified the higher count"
  fi
  if [ -n "$refusal" ]; then
    printf 'availability record — INSTRUMENT FAILURE, NOT A READING (#3810 / #3896)\n'
    printf 'The run fetch for this window was NOT verified complete, so no availability record is\n'
    printf 'rendered and nothing is published. A clipped fetch leaves the days it never reached with\n'
    printf 'zero runs, which would otherwise print as "NO samples delivered" — a claim that the PROBE\n'
    printf 'was not running. That claim would be false: a record that lies is worse than no record.\n'
    printf 'Reason: %s\n' "$refusal"
    return 1
  fi

  # The current UTC day is PARTIAL by construction — the daily workflow runs
  # mid-day. Scoring it against the full cron intent understates its cadence and
  # inflates the gap statistics (#3896 P2-6). Its intended count is the cron
  # intervals ELAPSED since 00:00 UTC, derived from CRON_PER_DAY rather than a
  # hardcoded 5-minute cadence, so a configured intent and today's denominator
  # can never contradict each other. Epoch % 86400 is seconds since UTC midnight.
  local today today_intended
  today="$(fmt_day "$now")"
  today_intended=$(( (now % 86400) * CRON_PER_DAY / 86400 ))
  # Floor at 1: we are always INSIDE at least the first cron interval of the
  # day, so at least one sample is due. Without the floor, a manual dispatch in
  # the first interval (00:00-00:05 UTC at the default cadence) would print
  # `delivered=N/0`, assert a ratio against a zero denominator, and render the
  # cadence column as the malformed value `n/a%` (#3896 P2-6 follow-on).
  [ "$today_intended" -ge 1 ] || today_intended=1

  local total_intended=0 total_delivered=0 total_failed=0 total_inconclusive=0
  local total_down_inc total_degraded_inc total_unknown_inc total_restarts undated_inc
  local total_down_min=0 total_degraded_min=0 total_unbounded=0 total_recovered=0 total_recovered_deg=0
  local zero_days=0 partial_days=0 gap_days_list="" inconclusive_kinds=""
  local day day_intended day_mark d_del d_fail d_inc d_decided del_ok
  local line inc_line inc_down inc_deg inc_unk d_rest d_dmin d_gmin d_open d_rec d_grec pct_avail
  # ⛔ AN AGGREGATION THAT DID NOT RUN IS NOT A ZERO (rounds 3, 5, 6). Each per-day pass
  # below converts its own failure into this refusal instead of an empty file: an empty
  # file renders as "no samples" / "0 minutes", which is a claim no run made.
  local agg_rc=0 agg_reason=""

  printf 'availability record — RECORD-ONLY baseline (#3810) — INSTRUMENT, NOT A GATE\n'
  printf 'generated: %s   window: %s UTC day(s) ending %s\n' "$(fmt_iso "$now")" "$days" "$today"
  printf 'cron intent: %s samples/day (the cron'"'"'s NOMINAL intent — the REAL resolution is the cadence factor printed below). GitHub delays/drops scheduled runs, so delivered < intended is NORMAL.\n' "$CRON_PER_DAY"
  printf 'failed_runs = runs whose probe verdict was non-zero (DOWN *or* DEGRADED). The incident columns keep the two kinds SEPARATE and are never summed.\n'
  printf 'availability denominator = runs that carried a VERDICT (success+failure). Inconclusive runs (cancelled/timed_out/in-progress/skipped) carry NO verdict on the service: they are excluded from BOTH the numerator and the denominator, counted in their own column, and disclosed as decided=K/D beside every figure. Counting them as available is how a cancelled run becomes a healthy-looking percentage.\n'
  printf 'run fetch integrity: COMPLETE — %s run(s) enumerated over %s bounded UTC-day slice(s); the API reported %s for the same slices and no slice reached the %s-result cap, so this window is complete. A clipped fetch is an INSTRUMENT FAILURE and renders nothing.\n' \
    "$i_enum" "$i_slices" "$i_rep" "$i_cap"
  printf '\n'

  # Per-day run counts, aggregated once. Every day in the window gets a row,
  # INCLUDING days with zero delivered samples: a gap must be distinguishable
  # from a down period, and an omitted row is exactly how a gap disappears.
  # Columns: day, delivered, failed, inconclusive(=no verdict).
  awk -F'\t' '{ d[$1]++; if ($2 == "failure") f[$1]++; else if ($2 != "success") o[$1]++ }
              END { for (k in d) printf "%s\t%d\t%d\t%d\n", k, d[k]+0, f[k]+0, o[k]+0 }' \
    "$RUN_TMP/runs.tsv" > "$RUN_TMP/day_runs.tsv" 2>/dev/null \
    || { agg_rc=1; agg_reason="the per-day run-count aggregation did not complete"; }

  # The distinct inconclusive conclusions, so "no verdict" is never a black box.
  awk -F'\t' '$2 != "success" && $2 != "failure" { c[$2]++ }
              END { for (k in c) printf "%s=%d ", k, c[k] }' \
    "$RUN_TMP/runs.tsv" > "$RUN_TMP/inconclusive_kinds.txt" 2>/dev/null || : > "$RUN_TMP/inconclusive_kinds.txt"

  # Per-day incident counts (bucketed by first_failure_ts — the day the
  # incident was FIRST observed, i.e. the day whose availability it explains).
  # Columns emitted: day, down, degraded, unknown, restart_attempts,
  #                  unbounded (no usable span), recovered_down, recovered_degraded.
  # MINUTES ARE NOT SUMMED HERE — see `build_span_union` below, which is the whole
  # reason it is a separate pass.
  # ⛔ THE SPAN IS SPLIT BY KIND, because this file's own invariant (`:51-52`) says DOWN
  # and DEGRADED are different product failures and are NEVER SUMMED. The first cut of this
  # aggregation summed both into `down_min`, which would have published "67 minutes of downtime"
  # on a day with zero DOWN incidents (ledger #11442, 2026-11-07) — and the harness could not
  # see it, because every fixture with a `closed_at` was `kind=down` (round-1 review P1).
  # ⛔ AND A SPAN THAT CANNOT BE USED IS DISCLOSED, NEVER DROPPED: an incident whose recovery
  # stamp is missing (`-`), a non-recovery (`x`), a NUMERIC SPAN EARLIER THAN the first failure,
  # or a usable span with NO parseable kind lands in `unbounded`, so no incident can be both
  # invisible and costed at 0 minutes (round-1 review P2, round-2 review P2 for the kind case).
  awk -F'\t' '{ day = $3; if (day == "" || day == "n/a") next;
                if ($2 == "down") dn[day]++; else if ($2 == "degraded") dg[day]++; else un[day]++;
                n = split($7, a, ","); c = 0; for (i = 1; i <= n; i++) if (a[i] != "") c++;
                rs[day] += c; seen[day] = 1;
                if ($4 ~ /^[0-9]+$/ && $5 ~ /^[0-9]+$/ && $5 >= $4) { if ($2 == "down") rcd[day]++; else if ($2 == "degraded") rcg[day]++; else op[day]++ } else { op[day]++ } }
              END { for (k in seen) printf "%s\t%d\t%d\t%d\t%d\t%d\t%d\t%d\n", k, dn[k]+0, dg[k]+0, un[k]+0, rs[k]+0, op[k]+0, rcd[k]+0, rcg[k]+0 }' \
    "$RUN_TMP/incidents.tsv" > "$RUN_TMP/day_incidents.tsv" 2>/dev/null \
    || { agg_rc=1; agg_reason="the per-day incident aggregation did not complete"; }

  build_span_union \
    || { agg_rc=1; agg_reason="the per-day downtime UNION pass did not complete"; }
  if [ "$agg_rc" -ne 0 ]; then
    printf 'availability record — INSTRUMENT FAILURE, NOT A READING (#3484)\n'
    printf 'A per-day aggregation pass did not complete, so the minute and incident columns have no\n'
    printf 'measurement behind them. Rendering zeros would assert "no downtime observed" — a figure\n'
    printf 'this run does not have. An unusable measurement is DISCLOSED, never costed at 0 minutes.\n'
    printf 'Reason: %s\n' "$agg_reason"
    fail "INSTRUMENT FAILURE — $agg_reason"
    return 1
  fi

  # Incidents that could not be dated AT ALL have no day to be bucketed into,
  # so they are disclosed on their own line rather than folded into a window
  # figure they do not belong to (#3896 P2-3). The window totals below stay
  # WINDOW-SCOPED — they are summed from the per-day buckets, exactly like the
  # per-day columns above them, so the totals row can never contradict the
  # columns it summarizes (a ledger-wide total would report incidents from
  # before the window the header names).
  undated_inc="$(awk -F'\t' '$3 == "" || $3 == "n/a" { n++ } END { print n + 0 }' "$RUN_TMP/incidents.tsv" 2>/dev/null || printf 0)"
  total_down_inc=0 total_degraded_inc=0 total_unknown_inc=0 total_restarts=0
  # ⛔ AN UNDATED INCIDENT IS A MEMBER OF THE UNBOUNDED CLASS (rounds 5-6). Both the header
  # (`:76-82`) and the PUBLISHED legend name "no parseable first_failure_ts" as one, but the
  # aggregator `next`s on a day it cannot bucket, so such a row reached no `op[]` key and the
  # record printed `0 incident(s) have NO usable span` while an undated — often still OPEN —
  # incident sat in the same output. Counting it here makes the column agree with its legend.
  total_unbounded=$(( total_unbounded + undated_inc ))

  # ── per-day table ──
  printf '%-12s %-9s %-9s %-9s %-9s %-10s %-56s %-9s %-13s %-12s %-9s %-9s %-13s %s\n' \
    'UTC day' 'delivered' 'intended' 'cadence' 'failed' 'inconcl.' 'sampled_availability (printed WITH its resolution)' 'down_inc' 'degraded_inc' 'unknown_inc' 'restarts' 'down_min' 'degraded_min' 'unbounded_inc'
  printf -- '-%.0s' $(seq 1 190); printf '\n'

  # Iterate the SAME day list the fetch walked, oldest first.
  while IFS= read -r day; do
    [ -n "$day" ] || continue
    if [ "$day" = "$today" ]; then day_intended="$today_intended"; day_mark='*'
    else day_intended="$CRON_PER_DAY"; day_mark=''; fi
    line="$(awk -F'\t' -v d="$day" '$1 == d { print $2 "\t" $3 "\t" $4; found = 1 } END { if (!found) print "0\t0\t0" }' "$RUN_TMP/day_runs.tsv")"
    d_del="$(printf '%s' "$line" | cut -f1)"
    d_fail="$(printf '%s' "$line" | cut -f2)"
    d_inc="$(printf '%s' "$line" | cut -f3)"
    d_decided=$(( d_del - d_inc ))
    inc_line="$(awk -F'\t' -v d="$day" '$1 == d { print $2 "\t" $3 "\t" $4 "\t" $5 "\t" $6 "\t" $7 "\t" $8; found = 1 } END { if (!found) print "0\t0\t0\t0\t0\t0\t0" }' "$RUN_TMP/day_incidents.tsv")"
    inc_down="$(printf '%s' "$inc_line" | cut -f1)"
    inc_deg="$(printf '%s' "$inc_line" | cut -f2)"
    inc_unk="$(printf '%s' "$inc_line" | cut -f3)"
    d_rest="$(printf '%s' "$inc_line" | cut -f4)"
    d_open="$(printf '%s' "$inc_line" | cut -f5)"
    d_rec="$(printf '%s' "$inc_line" | cut -f6)"
    d_grec="$(printf '%s' "$inc_line" | cut -f7)"
    # Minutes come from the UNION pass, keyed by (day, kind).
    d_dmin="$(awk -F'\t' -v d="$day" -v k="down" '$1 == d && $2 == k { print $3; found = 1 } END { if (!found) print 0 }' "$RUN_TMP/day_minutes.tsv" 2>/dev/null)"
    d_gmin="$(awk -F'\t' -v d="$day" -v k="degraded" '$1 == d && $2 == k { print $3; found = 1 } END { if (!found) print 0 }' "$RUN_TMP/day_minutes.tsv" 2>/dev/null)"
    d_dmin="${d_dmin:-0}"; d_gmin="${d_gmin:-0}"

    total_delivered=$((total_delivered + d_del))
    total_failed=$((total_failed + d_fail))
    total_inconclusive=$((total_inconclusive + d_inc))
    total_intended=$((total_intended + day_intended))
    total_down_inc=$((total_down_inc + inc_down))
    total_degraded_inc=$((total_degraded_inc + inc_deg))
    total_unknown_inc=$((total_unknown_inc + inc_unk))
    total_restarts=$((total_restarts + d_rest))
    total_down_min=$((total_down_min + d_dmin))
    total_degraded_min=$((total_degraded_min + d_gmin))
    total_unbounded=$((total_unbounded + d_open))
    total_recovered=$((total_recovered + d_rec))
    total_recovered_deg=$((total_recovered_deg + d_grec))

    if [ "$d_del" -eq 0 ]; then
      zero_days=$((zero_days + 1))
      gap_days_list="${gap_days_list}${gap_days_list:+, }${day}"
    elif [ "$d_del" -lt "$day_intended" ]; then
      partial_days=$((partial_days + 1))
    fi

    # THE LOAD-BEARING LINE: the availability figure is never printed alone —
    # it carries the delivered count, the cadence factor, and (now) the
    # verdict-carrying denominator its percentage is actually over. Removing any
    # of them makes the harness red. "no VERDICT" and "no SAMPLES" are different
    # facts and read differently.
    if [ "$d_del" -eq 0 ]; then
      pct_avail="n/a (NO samples delivered — a gap is not an uptime sample; delivered=0/${day_intended} cadence=$(pct 0 "$day_intended")%)"
    elif [ "$d_decided" -le 0 ]; then
      pct_avail="n/a (NO VERDICT — all ${d_del} delivered run(s) were inconclusive; a cancelled/in-progress run is not an observation, so it is neither available nor failed; delivered=${d_del}/${day_intended} cadence=$(pct "$d_del" "$day_intended")% decided=0/${d_del})"
    else
      del_ok=$(( d_del - d_fail - d_inc ))
      pct_avail="$(pct "$del_ok" "$d_decided")% (delivered=${d_del}/${day_intended} cadence=$(pct "$d_del" "$day_intended")% decided=${d_decided}/${d_del})"
    fi
    printf '%-12s %-9s %-9s %-9s %-9s %-10s %-56s %-9s %-13s %-12s %-9s %-9s %-13s %s\n' \
      "$day$day_mark" "$d_del" "$day_intended" "$(pct "$d_del" "$day_intended")%" "$d_fail" "$d_inc" "$pct_avail" "$inc_down" "$inc_deg" "$inc_unk" "$d_rest" "$d_dmin" "$d_gmin" "$d_open"
  done < "$RUN_TMP/window_days.txt"

  printf -- '-%.0s' $(seq 1 190); printf '\n'
  local total_decided=$(( total_delivered - total_inconclusive )) total_avail
  if [ "$total_delivered" -le 0 ]; then
    total_avail="n/a (delivered=0/${total_intended} cadence=$(pct 0 "$total_intended")%)"
  elif [ "$total_decided" -le 0 ]; then
    total_avail="n/a (NO VERDICT — all ${total_delivered} delivered run(s) were inconclusive; delivered=${total_delivered}/${total_intended} cadence=$(pct "$total_delivered" "$total_intended")% decided=0/${total_delivered})"
  else
    total_avail="$(pct "$(( total_delivered - total_failed - total_inconclusive ))" "$total_decided")% (delivered=${total_delivered}/${total_intended} cadence=$(pct "$total_delivered" "$total_intended")% decided=${total_decided}/${total_delivered})"
  fi
  printf '%-12s %-9s %-9s %-9s %-9s %-10s %-56s %-9s %-13s %-12s %-9s %-9s %-13s %s\n' \
    'TOTAL' "$total_delivered" "$total_intended" "$(pct "$total_delivered" "$total_intended")%" "$total_failed" "$total_inconclusive" \
    "$total_avail" "$total_down_inc" "$total_degraded_inc" "$total_unknown_inc" "$total_restarts" "$total_down_min" "$total_degraded_min" "$total_unbounded"

  printf '\n'
  printf '  * today (%s) is IN PROGRESS: intended=%s counts only the cron intervals elapsed since 00:00 UTC, so that row is not comparable to a complete day.\n' "$today" "$today_intended"

  # ── gaps ──
  printf '\n'
  printf 'GAPS — intervals where the probe delivered FEWER samples than its cron intended.\n'
  printf 'A low cadence means the PROBE was not running; it does NOT mean the service was well.\n'
  printf 'Neither is visible in a single availability percentage, which is why it is never printed alone.\n'
  printf '  zero-delivery days: %s of %s' "$zero_days" "$days"
  if [ -n "$gap_days_list" ]; then printf ' (%s)' "$gap_days_list"; fi
  printf '\n'
  printf '  partial days (fewer than the day%s intended delivered): %s of %s\n' "'s" "$partial_days" "$days"
  printf '  window delivery: %s/%s samples (cadence %s%%) — the probe'"'"'s real resolution is %s%% of the cron'"'"'s nominal intent\n' \
    "$total_delivered" "$total_intended" "$(pct "$total_delivered" "$total_intended")" "$(pct "$total_delivered" "$total_intended")"
  if [ -s "$RUN_TMP/inconclusive_kinds.txt" ]; then
    inconclusive_kinds="$(sed 's/ *$//' "$RUN_TMP/inconclusive_kinds.txt")"
    printf '  inconclusive runs (NO verdict — NOT availability, excluded from BOTH numerator and denominator): %s (%s)\n' "$total_inconclusive" "$inconclusive_kinds"
  else
    printf '  inconclusive runs (NO verdict — NOT availability, excluded from BOTH numerator and denominator): %s\n' "$total_inconclusive"
  fi

  # ── incidents ──
  printf '\n'
  printf 'INCIDENTS — from ledger issues carrying %s (kind and restarts read from each state block).\n' "$INCIDENT_STATE_MARKER"
  printf 'DOWN (no answer) and DEGRADED (answered wrongly) are SEPARATE product failures and are never summed.\n'
  printf 'DOWN_MIN = minutes the service was OBSERVED DOWN. DEGRADED_MIN is the same measure for DEGRADED incidents, in its OWN column — DOWN and DEGRADED are never summed. ⛔ MINUTES ARE WALL-CLOCK AND UNIONED: overlapping incidents are counted ONCE, and a minute is attributed to the UTC day it OCCURRED (the incident COUNT columns next to them attribute each incident to the day it was FIRST observed — a count belongs to the incident, a minute belongs to the clock). UNBOUNDED_INC = incidents that produce NO minutes and are disclosed here instead: still open, closed with an unreadable recovery stamp, closed as not_planned/duplicate (not a recovery), a recovery stamp that precedes the first failure, no parseable first_failure_ts, or a usable span with no parseable kind. Each carries its own label in the per-incident list, so an unknown end is never printed as "down right now". DOWN_MIN is a LOWER BOUND wherever UNBOUNDED_INC > 0. Resolution = the probe cadence printed above — a span shorter than one probe interval cannot be told apart from one interval.\n'
  printf '  incident totals: down=%s degraded=%s unknown=%s restart_attempts=%s\n' \
    "$total_down_inc" "$total_degraded_inc" "$total_unknown_inc" "$total_restarts"
  # ⛔ THE COUNT CLAUSE NAMES A POPULATION, SO IT MUST PRINT THAT POPULATION (review round 8 P2).
  # "FIRST OBSERVED inside this window" is `down_inc`/`degraded_inc` (every in-window incident);
  # `total_recovered` is the RECOVERED subset, which differs whenever anything in the window is
  # unbounded — so the round-7 rewording published "are 1 DOWN" on a day whose TOTAL row says
  # down_inc=4. A count the document contradicts one line above is the class this PR closes.
  printf '  recovered downtime: %s min DOWN and %s min DEGRADED — never summed. These are WALL-CLOCK minutes (overlapping incidents union, not add) attributed to the UTC day the minute OCCURRED, so a span that BEGAN BEFORE this window still contributes its in-window minutes. The incidents FIRST OBSERVED inside this window are %s DOWN and %s DEGRADED (of which %s and %s recovered) — a count belongs to the incident and a minute to the clock, so the two are STATED SEPARATELY and are not a ratio. %s incident(s) have NO usable span and contribute NOTHING, so every figure here is a LOWER BOUND wherever that count is non-zero.\n' \
    "$total_down_min" "$total_degraded_min" "$total_down_inc" "$total_degraded_inc" "$total_recovered" "$total_recovered_deg" "$total_unbounded"
  # ⛔ "SUMMED FROM THE DAY COLUMNS" IS NOT TRUE OF `unbounded_inc` (review round 8 P3): the
  # undated class is added to that TOTAL and has no day row, so the sentence must name the
  # exception instead of implying a reconciliation that cannot be performed.
  printf '  (those totals are WINDOW-SCOPED — summed from the day columns above, EXCEPT `unbounded_inc`,\n'
  printf '   which also counts incidents with no parseable `first_failure_ts` (they have no day row).\n'
  printf '   The list below covers the\n'
  printf '   whole ledger, so it can name an incident that is outside this window. They are NOT the sum of\n'
  printf '   the per-incident down_for= lines: overlapping spans union, and every minute is clipped to its\n'
  printf '   UTC day, so a line is evidence — not an addend.)\n'
  if [ "$undated_inc" -gt 0 ]; then
    # ⛔ THE DISCLOSURE MUST MATCH THE COLUMN (review round 7 P2). These incidents are
    # COUNTED in the TOTAL's `unbounded_inc` (`total_unbounded += undated_inc`) because
    # both the header and the legend name them as members of that class — so the old
    # "NOT counted in the window totals" sentence contradicted the row three lines above it.
    printf '  %s incident(s) carry no parseable first_failure_ts: listed below, counted in the TOTAL%s unbounded_inc but on NO day row (attributable to no UTC day) — a human-edited state block is warned about, NEVER silently dropped.\n' "$undated_inc" "'s"
  fi
  if [ ! -s "$RUN_TMP/incidents.tsv" ]; then
    printf '  (none found in the ledger)\n'
  else
    local inc_n inc_kind inc_day inc_ff inc_end inc_state inc_rest rest_disp restart_count down_disp ff_i now_i
    while IFS="$(printf '\t')" read -r inc_n inc_kind inc_day inc_ff inc_end inc_state inc_rest; do
      [ -n "$inc_n" ] || continue
      [ -n "$inc_day" ] || inc_day="n/a"
      if [ "$inc_day" = "n/a" ]; then
        warn "ledger incident #${inc_n} has no parseable first_failure_ts — listed, counted in the TOTAL's unbounded_inc but on no day row"
      fi
      if [ "$inc_kind" = "unknown" ]; then
        warn "ledger incident #${inc_n} has no parseable kind= — counted as unknown (never as down or degraded)"
      fi
      if [ -n "$inc_rest" ]; then rest_disp="$inc_rest"; else rest_disp="(none)"; fi
      restart_count="$(printf '%s' "$inc_rest" | tr ',' '\n' | grep -c '[0-9]' || true)"
      # The per-incident RAW span. NOTE it is NOT a component you can add up: the day and TOTAL
      # columns are the UNION of day-clipped intervals (an overlap is counted once, and a minute
      # is clipped to the window), so these lines are evidence, not an arithmetic decomposition.
      # ⛔ "STILL DOWN" AND "CLOSED, STAMP UNREADABLE" ARE DIFFERENT CLAIMS (review P2).
      # Both reach here as `inc_end == "-"`, so the label is chosen from `state`, not guessed:
      # asserting that a CLOSED incident is down right now — with a `now - first_failure`
      # elapsed time that grows for ever — is the most consequential false statement an
      # availability record can make, and it was what the first cut printed.
      # ⛔ NOTHING BELOW MAY DO ARITHMETIC ON A RAW LEDGER EPOCH (round-2 review P2).
      # `inc_ff` arrives verbatim from a human-editable issue body. Two reachable
      # consequences of using it directly: a numeric-but-UNRENDERABLE epoch
      # (`first_failure_ts=99999999999999999`) printed a NEGATIVE elapsed bound
      # ("open (>= -1666666666666637 min)") on the very incident the record's own warn
      # had just called unparseable; and a LEADING-ZERO value (`first_failure_ts=08`) is
      # bash OCTAL, so `$(( … - 08 ))` aborts the expansion under `set -u` and the render
      # dies — a human-edited body could stop the published record entirely. `to_int` is
      # this file's normalizer for exactly this class (`:171`); `inc_day == "n/a"` is this
      # file's existing "no parseable first_failure_ts" verdict (the width gate at the
      # top of this function is what makes `08` land there rather than at epoch 8).
      # ⛔ AND AN UNPARSEABLE KIND IS NOT A SPAN WE COUNT (round-2 review P2): the
      # aggregator can only bucket down/degraded, so anything else is disclosed as such
      # instead of being printed as minutes that no column contains.
      case "$inc_end" in
        -|'')
          if [ "$inc_day" = "n/a" ] || [ "$inc_ff" = "-" ]; then
            down_disp="unknown (no parseable first_failure_ts)"
          elif [ "$inc_state" = "closed" ]; then
            down_disp="closed, recovery stamp unreadable (NOT counted as downtime)"
          else
            # ⛔ A STAMP AHEAD OF NOW IS NOT AN ELAPSED TIME (review round 7 P3). The width
            # gate admits any 10-digit epoch, so a future `first_failure_ts` published
            # `open (>= -3505893 min elapsed)` — a NEGATIVE duration offered as downtime
            # evidence, the same false-statement class round 2 set out to remove.
            ff_i="$(to_int "$inc_ff" 0)"; now_i="$(now_epoch)"
            if [ "$ff_i" -gt "$now_i" ]; then
              down_disp="unknown (first_failure_ts is in the FUTURE — NOT counted)"
            else
              down_disp="open (>= $(( (now_i - ff_i) / 60 )) min elapsed, NOT counted)"
            fi
          fi
          ;;
        x) down_disp="excluded (closed as not_planned/duplicate — not a recovery)" ;;
        *)
          case "$inc_kind" in
            down|degraded)
              # ⛔ ONE `else` FOR TWO CAUSES WAS A WRONG DIAGNOSIS (review round 7 P3). "No
              # parseable first_failure_ts" and "the recovery precedes the failure" reach this
              # arm the same way (`inc_end` is numeric), and the header promises each unbounded
              # class its OWN label — so an undated row was told it held a clock inversion that
              # does not exist, and pointed at the wrong field to repair.
              # ⛔ AND A STAMP AHEAD OF NOW INVENTS A DURATION HERE TOO (review round 8, latent):
              # a future `first_failure_ts` with a later readable `closed_at` printed
              # `down_for=1226 min` for a day that is in no column. The guard is the same one,
              # applied before this arm instead of only in the open arm.
              ff_i="$(to_int "$inc_ff" 0)"
              if [ "$inc_day" = "n/a" ] || [ "$inc_ff" = "-" ]; then
                down_disp="unknown (no parseable first_failure_ts)"
              elif [ "$ff_i" -gt "$(now_epoch)" ]; then
                down_disp="unknown (first_failure_ts is in the FUTURE — NOT counted)"
              elif [ "$inc_end" -ge "$ff_i" ]; then
                down_disp="$(( (inc_end - ff_i) / 60 )) min"
              else
                down_disp="unusable (recovery stamp precedes the first failure — NOT counted)"
              fi
              ;;
            *) down_disp="unknown kind — NOT counted in any minutes column" ;;
          esac
          ;;
      esac
      printf '  #%s  first_observed=%s  kind=%s  restarts=%s  restart_attempts=%s  down_for=%s\n' \
        "$inc_n" "$inc_day" "$inc_kind" "$rest_disp" "$restart_count" "$down_disp"
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

  # Normalized like every other numeric input: a raw non-numeric value reaches
  # `$((days * CRON_PER_DAY))`, where bash reads it as a VARIABLE name and
  # `set -u` aborts with a bare "unbound variable" (#3896 P2-2).
  CRON_PER_DAY="$(to_int "${CRON_PER_DAY:-288}" "288")"
  [ "$CRON_PER_DAY" -ge 1 ] 2>/dev/null || CRON_PER_DAY=288

  # The window as an explicit day list, written ONCE and read by BOTH
  # fetch_runs and render_record, so the fetched set of days and the rendered
  # rows cannot drift apart.
  window_days "$days" "$now" > "$RUN_TMP/window_days.txt"

  fetch_runs || exit 1
  fetch_incidents || exit 1

  # render_record returns non-zero when the fetch could not be verified
  # complete: it prints an INSTRUMENT FAILURE banner (stdout is data) and
  # nothing is published. That is an instrument failure, never a low figure.
  if ! record="$(render_record "$days" "$now")"; then
    printf '%s\n' "$record"
    exit 1
  fi

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
