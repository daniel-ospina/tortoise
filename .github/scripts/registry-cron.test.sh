#!/usr/bin/env bash
# registry-cron.test.sh — self-check for .github/scripts/registry-cron.sh (#2796).
#
# Run: bash .github/scripts/registry-cron.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: stubs
# aws / curl / date on PATH and drives the driver with simulated /status,
# sweep and direct-R2 fixtures. No network, no R2, no GitHub.
#
# Coverage (the #2796 taxonomy):
#   1. deliberately off (measured-fresh pool)              → exit 0, silent
#   2. off because config broken (config_error non-null)   → incident + RED
#   3. off while the pool goes stale (age > driver-down)   → incident + RED
#   4. off because storage is down (storage_error)         → R2_DOWN + RED
#   5. enabled but 0 teams backed up while R2 has teams    → incident + RED
#      (the #2823 shape) — and it must NOT self-heal WATCHER_DOWN
#   6. enabled, 0 teams, R2 pool also empty (pre-beta)     → exit 0, silent
#   7. enabled + backed_up                                 → exit 0, self-heals
#   8. enabled + no_work (teams enumerated, none backed)   → incident + RED
#   9. dedup recurrence: 412 + object with no issue → re-files
#  10. dedup repeat:    412 + object with an OPEN issue → adopts (no duplicate)
#  11. R2 listing fails  → pool UNKNOWN, never read as empty/fresh
#  12. R2 listing fails while OFF → SWEEP_OFF_STALE + RED (unconfirmed pause)
#  13. R2 preflight down + 0 teams → R2_DOWN stays OPEN (no self-close loop)
#  14. lock held + stale last_sweep → SWEEP_NO_COVERAGE + RED
#  15. lock held + fresh last_sweep → healthy, silent
#  16. no_work + empty R2 pool → silent (pre-beta)
#  17. missing/non-JSON .enabled → SWEEP_NO_COVERAGE (fail CLOSED) + RED
#  18. watcher.running=false → WATCHER_DOWN filed (jq `//` false bug, #2843)
#  19. prefix without DEFAULT archive while OFF → SWEEP_OFF_STALE + RED
#  20. config_error secret redacted before PUBLICATION (#2796 review R2/R4)
#  21. 412 + R2 object with OPEN issue_number + empty search → NO duplicate
#  22. 412 + R2 object with CLOSED issue_number → re-files
#  23. WATCHER_DOWN self-heals on watcher EVIDENCE even on no_work (R3)
#  24. purge ride-along failure → RED
#  25. reconcile ride-along failure → RED
#  26. unquoted/bare secret runs are redacted too (security review)
#  27. last_sweep (graph_failures[].error) is redacted before publication
#  28. a 404 (deleted) tracked issue re-files; a 500 blip does not duplicate
#  29. resolve deletes BOTH spellings of the sentinel: canonical _.json
#      (the AlertStore's) and the legacy global.json (#2844)
#  30. a missing .watcher block neither files nor self-heals WATCHER_DOWN
#  31. degraded is healthy; no_eligible_teams is the no-coverage family
#  32. lock held with no usable last_sweep + pool data → SWEEP_NO_COVERAGE
#  33. sweep error / non-JSON body → SWEEP_NO_COVERAGE + RED (catch-all)
#  34. missing GITHUB_TOKEN → fail closed (no silent-deaf alerting)
#  35. a per-team listing failure is unmeasured, not a false stale
#  36. positive self-heals: SWEEP_CONFIG_ERROR / SWEEP_OFF_STALE resolve
#  37. multi-team pool: `--output text` is ONE tab-separated line (review P1)
#  38. held lock + UNMEASURED pool → SWEEP_NO_COVERAGE (review P1)
#  39. redaction of DSN/URI/Basic/lowercase/numbered secrets (security review)
#  40. the purge failure body is redacted (security review)
#  41. storage_error while ENABLED is loud, and blocks R2_DOWN self-heal
#  42. no_work with graph_totals.backed_up>0 is not a coverage gap
#  43. APP_DOWN is RED (any filing is RED)
#  44. an index read failure is unmeasured, not "no index"
#  45. a measured-fresh off pool resolves a stale R2_DOWN
#  46. SWEEP_NO_COVERAGE self-heals on a degraded/backed_up sweep
#  47. a per-team STALE filed while ENABLED also makes the run RED
#  48. `enum_failed` is the catch-all family → SWEEP_NO_COVERAGE + RED
#  49. no_eligible_teams + measured-EMPTY pool → silent (pre-beta)
#  50. redaction: prefixed/newline-split/schemeless-DSN shapes; no false
#      positives on `compatible:`/`patch:`/`author:`/graph ids/`TimeoutError`
#  51. the success branch backfills the R2 object with its issue_number (F7)
#  52. a failed listing blocks the R2_DOWN self-heal (F2)
#  53. a failed legacy-flat listing is unmeasured (R2_DOWN + RED)
#  54. an unparseable archive timestamp is never read as fresh
#  55. a stale watcher AGE alone (running=true, age>30) files WATCHER_DOWN
#  56. the measurable-empty lock branch is the silent one
#  57. an empty default prefix is a MEASURED-EMPTY result, not a global
#      R2_DOWN (#3659 defects 1+3)
#  58. a failed default listing still consumes the legacy-flat leg (#3659
#      defect 2)
#  59. a genuine listing failure surfaces the CLI stderr (#3659 defect 4)
#  60. an empty top-level pool (JSON `null`, absent CommonPrefixes) is a
#      measured-EMPTY pool, and a team genuinely named "None" survives
#  61. an empty legacy-flat prefix (JSON `null`) is measured-empty — it must
#      not enter flat classification and blank a measured default archive
#  62. an unparseable top-level listing is UNKNOWN, never an empty pool
#  63. the top-level listing stderr is captured (not /dev/null'd)
#  64. #3029: a FAILED search is not "no incident" — defer, never duplicate
#  65. #3029: an ERROR BODY (403 rate-limit) is not an empty result
#  66. #3029: an issue that merely MENTIONS the kind is never adopted
#  67. #3029: the kind must follow `[DR] ` immediately (boundary)
#  68. #3029: the subject must be the EXACT ` — ` segment
#  69. #3032: unsupported IfNoneMatch + ambiguous HEAD → LOUD, never a blind put
#  69b. #3032: a bare `412` in an unrelated error is NOT "already exists"
#  70. #3032: unsupported IfNoneMatch + EXISTING object → adopt, no duplicate
#  71. #3032: an UNRESOLVED dedup write fails LOUD, never search-only
#  64. a subject-less incident is written ONCE, under the CANONICAL `_.json`
#      (the AlertStore's spelling) — never the legacy `global.json` (#2844)
#  65. a legacy sentinel holding a CLOSED issue still lets the incident re-file
#      (#2844), so the alias does not swallow a recurrence
#  66. the driver's OWNERSHIP refusal in `resolve_global` is pinned (#3127): a
#      watcher-owned kind is refused (no search, no close); a driver-owned
#      kind resolves normally
#  67. `alert_key` canonicalizes the EMPTY (subject-less) id ONLY: a real
#      subject literally named `global`/`_` keeps its own single key and is
#      never an alias set — the round-7 P2 that otherwise gave one real team
#      two create-once points across bash and AlertStore (#2844)
#  68. a dedup no-op RECORDS the recurrence on the tracked issue (#3907) — a
#      repeat is never a silent no-op, and never a second issue
#  69. the recurrence counter is read from the ISSUE's own marked comments, so
#      it advances (#3 after two) and cannot be reset by the shared R2 object
#  70. the safety properties survive: a FIRST-TIME fault still files (and
#      records no recurrence), and a RECOVERED fault still self-heals (closes
#      the incident and deletes its dedup object)
#  71. a FAILED recurrence comment is never fatal and never duplicates: the run
#      stays RED, no second issue is filed, and the driver says the record did
#      not happen (so it cannot claim a comment it did not post). The failure is
#      modelled as the shape that ACTUALLY occurs — HTTP 403 with curl exit 0
#      (secondary rate limit / missing issues:write) — not a transport failure;
#      a stub that exits 1 makes the "no false record" assertion vacuous for
#      every error a real GitHub returns.
#  72. an OUTSIDER marker comment cannot inflate the recurrence counter (P2:
#      the marker is public; only the Actions bot's marked comments count)
#  77. #5028: the ride-along skip names the ACTUAL status — already_running is
#      the lock case; a non-lock skip (driver_timeout/empty_response) must
#      never assert a held lock
#  78. #5028: a sweep --max-time timeout (curl rc 28) names driver_timeout and
#      reports the elapsed seconds in the log line AND the filed incident body;
#      the ride-along stays skipped
#  79. #5028: an empty sweep body names empty_response (not a held lock)
#  80. #5028: the sweep OUTCOME is logged untruncated even when `per_team`
#      (the org census) precedes it in the payload
#  81. #5028: the outcome's org census is `unknown` (never blank) when /status
#      carries no object `per_team`; an object still reports the real count
#
# Fixtures are simulated; the real driver defers nothing.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRIVER="$SCRIPT_DIR/registry-cron.sh"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  ❌ $1"; }
assert_eq() { # <actual> <expected> <label>
  if [ "$1" = "$2" ]; then ok "$3"; else bad "$3 (got '$1', want '$2')"; fi
}
assert_contains() { # <haystack> <needle> <label>
  if printf '%s' "$1" | grep -qF -- "$2"; then ok "$3"; else bad "$3 (missing: $2)"; fi
}
assert_not_contains() { # <haystack> <needle> <label>
  if printf '%s' "$1" | grep -qF -- "$2"; then bad "$3 (unexpected: $2)"; else ok "$3"; fi
}
assert_match() { # <haystack> <regex> <label>
  if printf '%s' "$1" | grep -qE -- "$2"; then ok "$3"; else bad "$3 (no match: $2)"; fi
}
assert_not_match() { # <haystack> <regex> <label>
  if printf '%s' "$1" | grep -qE -- "$2"; then bad "$3 (unexpected match: $2)"; else ok "$3"; fi
}
assert_filed() { # <log> <KIND> <label> — a create POST whose body carries KIND
  # NB: matching a bare KIND against the log is confounded by the GitHub SEARCH
  # URL (which carries the kind). Assert on the create POST instead.
  if printf '%s' "$1" | grep -qE "GH POST .*/issues .*${2}"; then ok "$3"; else bad "$3 (no incident POST for ${2})"; fi
}

FIX="$(mktemp -d)"
trap 'rm -rf "$FIX"' EXIT
BIN="$FIX/bin"
mkdir -p "$BIN"
LOG="$FIX/calls.log"
: > "$LOG"

# ── stub: aws ───────────────────────────────────────────────────────────────
cat > "$BIN/aws" <<'AWS_EOF'
#!/usr/bin/env bash
op="${2:-}"
args=("$@")
argval() { # <flag> -> next arg
  local i
  for ((i=0; i<${#args[@]}; i++)); do
    if [ "${args[$i]}" = "$1" ]; then printf '%s' "${args[$((i+1))]}"; return 0; fi
  done
}
echo "AWS $op key=$(argval --key) prefix=$(argval --prefix)" >> "$STUB_LOG"
# Record the put body so a test can PROVE the backfilled issue_number (as
# opposed to the create-once attempt, which writes issue_number:null —
# review: the bare key match was vacuous).
b="$(argval --body)"
[ -n "$b" ] && [ -f "$b" ] && echo "AWS $op body=$(cat "$b" 2>/dev/null)" >> "$STUB_LOG"
case "$op" in
  head-bucket)  [ "${STUB_R2_DOWN:-0}" = "1" ] && exit 1 || exit 0 ;;
  list-objects-v2)
    p="$(argval --prefix)"
    case "$p" in
      "backups/")
        [ "${STUB_LIST_FAIL:-0}" = "1" ] && { echo "An error occurred (AccessDenied) when calling the ListObjectsV2 operation: Access Denied" >&2; exit 1; }
        # STUB_TOP_NOT_JSON: an exit-0 body the driver's jq cannot decode — the
        # top-level listing must read as UNKNOWN, never as an empty pool.
        [ "${STUB_TOP_NOT_JSON:-0}" = "1" ] && { printf 'not json at all'; exit 0; }
        if [ "$(argval --output)" = "json" ]; then
          # #3659: model the CLI faithfully — an absent CommonPrefixes renders
          # as JSON `null`; otherwise the tab-separated fixture prefixes are
          # rendered as a JSON array.
          if [ -z "${R2_TEAMS:-}" ]; then printf 'null'; else
            printf '%s' "$R2_TEAMS" | tr '\t' '\n' | jq -Rsc 'split("\n") | map(select(. != ""))'
          fi
        else
          # `--output text` renders a null JMESPath result as the literal
          # "None" — mirror it so the pre-fix text path is faithfully modelled
          # (and case 60 actually discriminates).
          if [ -z "${R2_TEAMS:-}" ]; then printf 'None'; else printf '%s' "$R2_TEAMS"; fi
        fi ;;
      */default/)
        [ "${STUB_LIST_FAIL_TEAM:-0}" = "1" ] && { echo "An error occurred (AccessDenied) when calling the ListObjectsV2 operation: Access Denied" >&2; exit 1; }
        # Per-team override so a multi-team case can distinguish WHICH team the
        # driver measured (review R1 test-integrity): with one shared listing
        # every prefix returns the same value and the tab-split regression is
        # invisible (the buggy loop measures only the LAST team).
        case "$p" in
          "backups/teamZ/default/") dval="${R2_DEFAULT_LIST_Z:-${R2_DEFAULT_LIST:-}}" ;;
          "backups/teamA/default/") dval="${R2_DEFAULT_LIST_A:-${R2_DEFAULT_LIST:-}}" ;;
          *) dval="${R2_DEFAULT_LIST:-}" ;;
        esac
        # #3659: emulate awscli's JMESPath evaluation faithfully. An empty
        # prefix means S3 omits `Contents` entirely, so a sort_by()/max_by()
        # aggregator over it raises JMESPathTypeError and the CLI exits
        # non-zero (the driver read that as a storage outage). A total query
        # (plain projection, no aggregator) returns `null`/`[]` instead, and
        # `--output json` renders the result as JSON. Simplification: the
        # fixture has no Contents-level detail, so "prefix has objects but no
        # dump.enc" is not modelled — the empty fixture stands in for an
        # absent Contents (the production empty-prefix path).
        if [ -z "$dval" ]; then
          case "$(argval --query)" in
            *sort_by*|*max_by*)
              echo "JMESPathTypeError: In function sort_by(), invalid type for value: None, expected one of: ['array'], received: \"null\"" >&2
              exit 255 ;;
          esac
        fi
        case "$(argval --output)" in
          json) if [ -z "$dval" ]; then printf 'null'; else printf '[\"%s\"]' "$dval"; fi ;;
          *)    printf '%s' "$dval" ;;
        esac ;;
      backups/*/2)
        [ "${STUB_FLAT_FAIL:-0}" = "1" ] && { echo "An error occurred (AccessDenied) when calling the ListObjectsV2 operation: Access Denied" >&2; exit 1; }
        # #3659: an EMPTY flat prefix renders as JSON `null` (absent Contents),
        # not `[]` — mirror the real CLI so the empty-prefix path is exercised.
        if [ -n "${R2_FLAT_LIST:-}" ]; then printf '%s' "$R2_FLAT_LIST"; else printf 'null'; fi ;;
      *)            printf '' ;;
    esac
    ;;
  put-object)
    # #3032 knobs: a client that rejects the conditional flag, a store that
    # fails the write outright, and an unrelated error that merely contains the
    # digits 412. All three are distinguished from the real 412 race.
    if [ "${STUB_NO_IFNONEMATCH:-0}" = "1" ] && printf '%s' "${args[*]}" | grep -q -- '--if-none-match'; then
      echo "Unknown options: --if-none-match" >&2
      exit 2
    fi
    if [ "${STUB_PUT_412_SUBSTRING:-0}" = "1" ] && printf '%s' "${args[*]}" | grep -q -- '--if-none-match'; then
      echo "An error occurred (InternalError) when calling the PutObject operation: RequestId 4120xyz" >&2
      exit 1
    fi
    if [ "${STUB_PUT_FAIL:-0}" = "1" ]; then
      echo "An error occurred (InternalError) when calling the PutObject operation" >&2
      exit 1
    fi
    [ "${STUB_412:-0}" = "1" ] && exit 1 || exit 0 ;;
  head-object)  [ "${STUB_HEAD_EXISTS:-${STUB_412:-0}}" = "1" ] && exit 0 || exit 1 ;;
  get-object)
    # STUB_INDEX_FAIL emulates a NON-404 failure reading the legacy-flat index
    # (a transient S3 error) so the driver must treat the pool as unmeasured.
    if [ "${STUB_INDEX_FAIL:-0}" = "1" ] && case "$(argval --key)" in *legacy-flat-index*) true ;; *) false ;; esac; then
      echo "An error occurred (InternalError) when calling the GetObject operation" >&2
      exit 1
    fi
    # STUB_GET_ONLY_CANONICAL: answer STUB_GET_BODY for the CANONICAL `_.json`
    # object only. A test that must exercise the create-once write
    # (`r2_put_once`) uses this so `file_alert`'s #2844 legacy-alias pre-read
    # (which consults `global.json`) does not adopt the object first.
    if [ "${STUB_GET_ONLY_CANONICAL:-0}" = "1" ] && ! printf '%s' "${args[*]}" | grep -q '/_\.json'; then
      exit 1
    fi
    printf '%s' "${STUB_GET_BODY:-}" ;;
  delete-object) exit 0 ;;
  *) exit 0 ;;
esac
exit 0
AWS_EOF

# ── stub: date (GNU-compatible enough for the driver) ───────────────────────
cat > "$BIN/date" <<'DATE_EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "-d" ]; then
  arg="${2:-}"
  python3 -c "import sys,datetime;print(int(datetime.datetime.fromisoformat(sys.argv[1].replace('Z','+00:00')).timestamp()))" "$arg" 2>/dev/null \
    || /bin/date -u -d "$arg" +%s 2>/dev/null \
    || echo 0
  exit 0
fi
exec /bin/date "$@"
DATE_EOF

# ── stub: curl ──────────────────────────────────────────────────────────────
cat > "$BIN/curl" <<'CURL_EOF'
#!/usr/bin/env bash
out_file=""; write_fmt=""; url=""; data=""; method="GET"; fail=0
args=("$@"); i=0
while [ $i -lt ${#args[@]} ]; do
  a="${args[$i]}"
  case "$a" in
    -o) out_file="${args[$((i+1))]:-}"; i=$((i+2)) ;;
    -w) write_fmt="${args[$((i+1))]:-}"; i=$((i+2)) ;;
    -X) method="${args[$((i+1))]:-GET}"; i=$((i+2)) ;;
    -d) data="${args[$((i+1))]:-}"; i=$((i+2)) ;;
    -H|-m|--data-urlencode|--data|--header|--max-time|-u) i=$((i+2)) ;;
    -f|--fail) fail=1; i=$((i+1)) ;;
    -s|-sS|-S|-L|-k) i=$((i+1)) ;;
    *) url="$a"; i=$((i+1)) ;;
  esac
done

emit() { # body code
  local body="$1" code="${2:-200}"
  [ -n "$out_file" ] && printf '%s' "$body" > "$out_file"
  if [ -n "$write_fmt" ]; then printf '%s' "$code"; else [ -z "$out_file" ] && printf '%s' "$body"; fi
}
# NB: defaults live in variables — a literal JSON object inside ${VAR:-{...}}
# is mis-parsed (the first '}' closes the expansion, leaking the rest).
DEFAULT_STATUS='{}'
DEFAULT_SWEEP='{"status":"backed_up"}'
DEFAULT_PURGE='{"status":"ok","teams_purged":0}'
gh_log() { # line
  if [ -n "$data" ]; then echo "GH $method $url $data" >> "$STUB_LOG"; else echo "GH $method $url" >> "$STUB_LOG"; fi
}

case "$url" in
  *api.telegram.org*) exit 0 ;;
  *api.github.com*)
    gh_log
    case "$url" in
      */search/issues*)
        # #3029/#3032 knobs: a transport failure, an error body (403 rate-limit),
        # a hand-written result set, or an unrelated issue whose title merely
        # mentions the kind (the #2844 adoption shape). `emit` carries the HTTP
        # status: the merged `gh_find_open` reads it through gh_search_items, so
        # a stub that printed a body raw would be read as a code-less failure.
        if [ "${STUB_GH_SEARCH_FAIL:-0}" = "1" ]; then
          exit 1
        elif [ -n "${STUB_GH_SEARCH_BODY:-}" ]; then
          emit "$STUB_GH_SEARCH_BODY" "${STUB_SEARCH_CODE:-200}"
        else
          items="[]"
          for kind in SWEEP_CONFIG_ERROR SWEEP_OFF_STALE SWEEP_NO_COVERAGE WATCHER_DOWN APP_DOWN R2_DOWN STALE; do
            var="GH_ISSUE_$kind"
            val="${!var:-}"
            if [ -n "$val" ] && printf '%s' "$url" | grep -q "$kind"; then
              items="[{\"number\":$val,\"title\":\"${GH_ISSUE_TITLE:-[DR] $kind}\"}]"
              break
            fi
          done
          # GH_SEARCH_FILLER=1: page 1 is a FULL page of 100 machine-authored
          # look-alikes, so the exact match can only be found on page 2 — a
          # single-page read files a duplicate. NB the match must be `&page=1`:
          # `page=1` is also a substring of `per_page=100`.
          if [ "${GH_SEARCH_FILLER:-0}" = "1" ]; then
            case "$url" in
              *'&page=1')
                filler="$(python3 -c 'import json;print(",".join(json.dumps({"number":1000+i,"title":"[DR] OTHER — filler %d" % i}) for i in range(100)))')"
                emit "{\"items\":[$filler]}" "${STUB_SEARCH_CODE:-200}"
                exit 0 ;;
            esac
          fi
          emit "{\"items\":$items}" "${STUB_SEARCH_CODE:-200}"
        fi ;;
      */issues/*/comments*)
        # #3907: occurrence counting GETs the issue's comments; the comment
        # POST is the recording write. Distinguish by method so the counting
        # walk is actually exercised (a stub that answered the POST shape here
        # would make the counter read 0 forever).
        if [ "$method" = "GET" ]; then
          # emit honours -o/-w: the counter reads the body through the
          # status-checked primitive, so a stub that printed the body raw would
          # hand the JSON to the code parser and silently count 0.
          emit "${STUB_COMMENTS_JSON:-[]}" "${STUB_COMMENTS_CODE:-200}"
        else
          # REAL curl exits 0 on an HTTP 4xx/5xx; only `--fail` turns that into
          # exit 22. This stub therefore models the HTTP STATUS (default 201),
          # NOT the exit code: a stub that `exit 1`s here is a TRANSPORT failure
          # and makes case 71's assertion vacuous for the shape that actually
          # occurs (a 403 from the comments endpoint, exit 0).
          #   STUB_COMMENT_CODE=403  → HTTP 403, exit 0 (the real failure shape)
          #   STUB_COMMENT_FAIL=1    → transport failure, exit 1 (curl itself died)
          [ "${STUB_COMMENT_FAIL:-0}" = "1" ] && exit 1
          emit '{}' "${STUB_COMMENT_CODE:-201}"
          case "${STUB_COMMENT_CODE:-201}" in
            2[0-9][0-9]) ;;
            *) [ "$fail" = "1" ] && exit 22 ;;
          esac
        fi ;;
      */issues/*)
        if [ "$method" = "GET" ]; then
          # emit honours -o/-w: gh_issue_open asks for the code AND the body in
          # ONE status-checked call.
          emit "{\"state\":\"${GH_ISSUE_STATE:-open}\"}" "${STUB_ISSUE_CODE:-200}"
        else
          # PATCH (close). emit honours -w so gh_close's HTTP-code check works;
          # STUB_GH_CLOSE_CODE / STUB_CLOSE_CODE both simulate a rate-limit/5xx
          # close failure (the two suites spell the knob differently).
          cbody='{}'; emit "$cbody" "${STUB_GH_CLOSE_CODE:-${STUB_CLOSE_CODE:-200}}"
        fi ;;
      */issues)
        # A real POST returns the created issue; the HTTP status (not the exit
        # code) is what the status-checked primitive reads. STUB_CREATE_CODE
        # models a 403/5xx answered with exit 0.
        emit "{\"number\":${GH_NEW_ISSUE:-900}}" "${STUB_CREATE_CODE:-201}" ;;
      *) printf '{}' ;;
    esac
    ;;
  *"/v1/internal/backups/status"*)
    [ "${STUB_APP_DOWN:-0}" = "1" ] && exit 0
    sbody="${STUB_STATUS_BODY:-$DEFAULT_STATUS}"; emit "$sbody" ;;
  *"/v1/internal/backups/sweep"*)
    # STUB_SWEEP_RC models curl's OWN exit for the sweep call. 28 = --max-time
    # exceeded (a real timeout writes NO body and returns non-zero); 0 with an
    # empty body models the empty_response shape. Without this the driver's
    # driver_timeout / empty_response arms are unreachable and any assertion
    # about them is vacuous (#5028).
    if [ -n "${STUB_SWEEP_RC:-}" ]; then printf ''; exit "$STUB_SWEEP_RC"; fi
    sbody="${STUB_SWEEP_BODY:-$DEFAULT_SWEEP}"; emit "$sbody" ;;
  *"/v1/internal/backups/purge"*)
    sbody="${STUB_PURGE_BODY:-$DEFAULT_PURGE}"; emit "$sbody" "${STUB_PURGE_CODE:-200}" ;;
  *"/v1/internal/reconcile"*)
    emit '{}' "${STUB_RECONCILE_CODE:-200}" ;;
  *"/v1/internal/driver/heartbeat"*) printf '{}' ;;
  *) printf '{}' ;;
esac
exit 0
CURL_EOF

chmod +x "$BIN/aws" "$BIN/date" "$BIN/curl"
export PATH="$BIN:$PATH"
export STUB_LOG="$LOG"

# ── fixture timestamps ──────────────────────────────────────────────────────
TS_RECENT="$(python3 -c "import datetime;print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ'))")"
TS_STALE="$(python3 -c "import datetime;print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=500)).strftime('%Y-%m-%dT%H:%M:%SZ'))")"
export TS_RECENT TS_STALE

# ── driver env ──────────────────────────────────────────────────────────────
export INTERNAL_API_URL="https://api.example.test"
export FASTAPI_INTERNAL_KEY="test-key"
export R2_ACCOUNT_ID="acct"
export R2_BUCKET="tortoise-backups"
export GH_REPO="daniel-ospina/tortoise"
export GITHUB_TOKEN="test-token"
export BACKUP_STALE_THRESHOLD_MIN="90"
export BACKUP_DRIVER_DOWN_THRESHOLD_MIN="240"
unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID || true

reset_case() {
  : > "$LOG"
  unset STUB_STATUS_BODY STUB_SWEEP_BODY STUB_SWEEP_RC STUB_PURGE_BODY STUB_PURGE_CODE \
        STUB_RECONCILE_CODE STUB_412 STUB_APP_DOWN STUB_R2_DOWN STUB_GET_BODY \
        SIMULATE_APP_DOWN STUB_GH_SEARCH_FAIL STUB_GH_SEARCH_BODY \
        STUB_NO_IFNONEMATCH STUB_HEAD_EXISTS STUB_PUT_FAIL STUB_PUT_412_SUBSTRING \
        STUB_GET_ONLY_CANONICAL \
        STUB_GH_CLOSE_CODE \
        STUB_LIST_FAIL STUB_LIST_FAIL_TEAM STUB_FLAT_FAIL STUB_INDEX_FAIL GH_ISSUE_STATE STUB_ISSUE_CODE \
        STUB_TOP_NOT_JSON \
        STUB_SEARCH_CODE STUB_CREATE_CODE STUB_CLOSE_CODE GH_SEARCH_FILLER \
        GH_ISSUE_TITLE STUB_COMMENTS_CODE \
        GH_SEARCH_JSON GH_NEW_ISSUE R2_TEAMS R2_DEFAULT_LIST R2_FLAT_LIST \
        R2_DEFAULT_LIST_Z R2_DEFAULT_LIST_A \
        GH_ISSUE_SWEEP_CONFIG_ERROR GH_ISSUE_SWEEP_OFF_STALE GH_ISSUE_SWEEP_NO_COVERAGE \
        GH_ISSUE_WATCHER_DOWN GH_ISSUE_APP_DOWN GH_ISSUE_R2_DOWN GH_ISSUE_STALE \
        STUB_COMMENTS_JSON STUB_COMMENT_FAIL STUB_COMMENT_CODE || true
  export R2_FLAT_LIST="[]"
}

run_driver() { # -> sets RC
  # Invoke the driver directly (it carries a shebang + the exec bit) rather
  # than `bash "$DRIVER"`: a $(bash <script>) command substitution is read by
  # the agent-infra worktree guard as unverifiable "git content" and blocks the
  # harness locally (#1484 false positive — filed separately).
  set +e
  OUT="$("$DRIVER" 2>&1)"
  RC=$?
  set -e
}

status_body() { # enabled config storage [watcher_running] [watcher_age] [last_sweep_at]
  printf '{"enabled":%s,"config_error":%s,"storage_error":%s,"per_team":{},"last_sweep":{"last_sweep_at":"%s","last_team_count":0},"watcher":{"running":%s,"age_minutes":%s}}' \
    "$1" "$2" "$3" "${6:-2026-08-09T23:30:00Z}" "${4:-true}" "${5:-1}"
}

echo "registry-cron.test.sh — #2796 taxonomy"

# ── 1. deliberately off (no config error, fresh pool) → silent exit 0 ───────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false null null)"
export STUB_SWEEP_BODY='{"status":"backed_up"}'
run_driver
assert_eq "$RC" 0 "1. deliberate-off exits 0"
assert_not_contains "$(cat "$LOG")" "GH POST" "1. deliberate-off files nothing"
assert_contains "$OUT" "deliberately disabled" "1. deliberate-off logs the kill-switch as deliberate"

# ── 2. off because config broken → SWEEP_CONFIG_ERROR + RED ─────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
run_driver
assert_eq "$RC" 1 "2. config-broken off exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_CONFIG_ERROR "2. config-broken files SWEEP_CONFIG_ERROR"
assert_contains "$OUT" "kill-switch is NOT an operator decision" "2. config-broken is not treated as a pause"

# ── 3. off while the pool goes stale → SWEEP_OFF_STALE + RED ────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_STALE"
export STUB_STATUS_BODY="$(status_body false null null)"
run_driver
assert_eq "$RC" 1 "3. off-while-stale exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_OFF_STALE "3. off-while-stale files SWEEP_OFF_STALE"
assert_contains "$OUT" "newest archive" "3. direct leg still reports the measured age"

# ── 4. off because storage is down → R2_DOWN + RED ──────────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false null '"bucket unreachable"')"
run_driver
assert_eq "$RC" 1 "4. storage-broken off exits RED (1)"
assert_filed "$(cat "$LOG")" R2_DOWN "4. storage-broken files R2_DOWN"

# ── 5. enabled but 0 teams backed up while R2 has teams → SWEEP_NO_COVERAGE ─
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null false 999)"
export STUB_SWEEP_BODY='{"status":"no_teams","teams_backed_up":0}'
export GH_ISSUE_WATCHER_DOWN=55
run_driver
assert_eq "$RC" 1 "5. enabled-no-coverage exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "5. enabled-no-coverage files SWEEP_NO_COVERAGE"
assert_match "$(cat "$LOG")" "AWS put-object key=ops/alerts/WATCHER_DOWN/_.json" "5. the stale watcher incident is recorded"
assert_not_match "$(cat "$LOG")" "GH PATCH .*/issues/55" "5. enabled-no-coverage does NOT self-heal WATCHER_DOWN"

# ── 6. enabled, 0 teams, R2 pool also empty (chronic pre-beta) → silent ─────
reset_case
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_teams","teams_backed_up":0}'
run_driver
assert_eq "$RC" 0 "6. pre-beta 0-teams exits 0"
assert_not_contains "$(cat "$LOG")" "SWEEP_NO_COVERAGE" "6. pre-beta 0-teams files nothing"

# ── 7. enabled + backed_up → self-heal closes WATCHER_DOWN ──────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export GH_ISSUE_WATCHER_DOWN=77
run_driver
assert_eq "$RC" 0 "7. healthy run exits 0"
assert_match "$(cat "$LOG")" "GH PATCH .*/issues/77" "7. healthy run closes the incident"

# ── 8. enabled + no_work (teams enumerated, none backed up) → RED ───────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_work","teams_backed_up":0}'
run_driver
assert_eq "$RC" 1 "8. no_work exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "8. no_work files SWEEP_NO_COVERAGE"

# ── 9. dedup recurrence: 412 + no open issue → re-files ─────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
run_driver
assert_eq "$RC" 1 "9. recurrence exits RED (1)"
assert_match "$(cat "$LOG")" "GH POST .*/issues \{" "9. recurrence with a closed issue RE-FILES (not swallowed)"

# ── 10. dedup repeat: 412 + open issue → adopt, no duplicate ────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
export GH_ISSUE_SWEEP_CONFIG_ERROR=42
run_driver
assert_eq "$RC" 1 "10. repeat is still RED (1)"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "10. repeat adopts the open issue (no duplicate)"

# ── 11. R2 listing failure → pool UNKNOWN, not empty ────────────────────────
reset_case
export STUB_LIST_FAIL=1
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_teams","teams_backed_up":0}'
run_driver
assert_eq "$RC" 1 "11. unmeasurable pool + no_teams exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "11. unmeasurable pool files SWEEP_NO_COVERAGE"
assert_contains "$OUT" "pool state is UNKNOWN" "11. the failed listing is recorded as unknown"

# ── 12. R2 listing failure while OFF → SWEEP_OFF_STALE + RED ────────────────
# Review R5/Agent1: an unmeasurable pool is NOT a confirmed deliberate pause,
# so the driver files and goes RED (unknown ≠ fresh).
reset_case
export STUB_LIST_FAIL=1
export STUB_STATUS_BODY="$(status_body false null null)"
run_driver
assert_eq "$RC" 1 "12. off + unmeasurable pool exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_OFF_STALE "12. an unmeasured pool while off files SWEEP_OFF_STALE"
assert_contains "$OUT" "cannot be confirmed" "12. the driver says the pause is unconfirmed"

# ── 13. R2 preflight down + 0 teams → R2_DOWN stays OPEN ────────────────────
reset_case
export STUB_R2_DOWN=1
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_teams","teams_backed_up":0}'
export GH_ISSUE_R2_DOWN=88
run_driver
assert_eq "$RC" 1 "13. R2 down + 0 teams exits RED (1)"
assert_match "$(cat "$LOG")" "AWS put-object key=ops/alerts/R2_DOWN/_.json" "13. R2_DOWN is recorded"
assert_not_match "$(cat "$LOG")" "GH PATCH .*/issues/88" "13. a failed R2 probe does NOT self-close the R2_DOWN it filed"

# ── 14. 202 lock held + stale last_sweep → SWEEP_NO_COVERAGE ────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null true 1 "$TS_STALE")"
export STUB_SWEEP_BODY='{"status":"already_running"}'
run_driver
assert_eq "$RC" 1 "14. stuck lock exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "14. stuck lock files SWEEP_NO_COVERAGE"

# ── 15. 202 lock held + fresh last_sweep → healthy ──────────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null true 1 "$TS_RECENT")"
export STUB_SWEEP_BODY='{"status":"already_running"}'
run_driver
assert_eq "$RC" 0 "15. healthy lock exits 0"
assert_not_contains "$(cat "$LOG")" "SWEEP_NO_COVERAGE" "15. healthy lock files nothing"

# ── 16. no_work + empty R2 pool → silent (pre-beta) ─────────────────────────
reset_case
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_work","teams_backed_up":0}'
run_driver
assert_eq "$RC" 0 "16. no_work + empty pool exits 0"
assert_not_contains "$(cat "$LOG")" "SWEEP_NO_COVERAGE" "16. no_work + empty pool files nothing"

# ── 17. missing / non-JSON .enabled → fail CLOSED ───────────────────────────
reset_case
export STUB_STATUS_BODY='{"config_error":null,"storage_error":null}'
run_driver
assert_eq "$RC" 1 "17a. missing .enabled exits RED (1)"
assert_contains "$(cat "$LOG")" "unclassifiable" "17a. missing .enabled files the unclassifiable incident"
reset_case
export STUB_STATUS_BODY='Internal Server Error'
run_driver
assert_eq "$RC" 1 "17b. non-JSON 200 exits RED (1)"
assert_contains "$(cat "$LOG")" "unclassifiable" "17b. non-JSON 200 files the unclassifiable incident"

# ── 18. watcher.running=false → WATCHER_DOWN (jq `//` false bug, #2843) ─────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null false 1)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
run_driver
assert_eq "$RC" 1 "18. backed_up is RED while the staleness watcher is dead (loud)"
assert_match "$(cat "$LOG")" 'GH POST .*/issues .*WATCHER_DOWN' "18. running=false is honored and files WATCHER_DOWN"
assert_contains "$OUT" "exiting RED" "18. the run is RED because it filed an incident"

# ── 19. prefix without DEFAULT archive while OFF → SWEEP_OFF_STALE ───────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST=""
export STUB_STATUS_BODY="$(status_body false null null)"
run_driver
assert_eq "$RC" 1 "19. never-backed-up pool + OFF exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_OFF_STALE "19. never-backed-up pool files SWEEP_OFF_STALE"

# ── 20. config_error secret is redacted before publication ──────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY='{"enabled":false,"config_error":"must be base64 (got '"'"'SECRETKEY9'"'"'...)","storage_error":null,"per_team":{},"last_sweep":{"last_sweep_at":"2026-08-09T23:30:00Z","last_team_count":0},"watcher":{"running":true,"age_minutes":1}}'
run_driver
assert_eq "$RC" 1 "20. config-broken off exits RED (1)"
assert_contains "$(cat "$LOG")" "<redacted>" "20. the published body carries the redaction marker"
assert_not_contains "$(cat "$LOG")" "SECRETKEY9" "20. the key prefix is NOT published"
assert_not_contains "$OUT" "SECRETKEY9" "20. the key prefix is NOT logged either"

# ── 21. 412 + R2 object with an OPEN issue_number → no duplicate ────────────
# After #2844 this path is reached via the alias pre-check: the stub's r2_get
# answers for the legacy `global.json` too, so a legacy sentinel holding an OPEN
# issue is adopted before the driver ever attempts its own create-once.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
export STUB_GET_BODY='{"kind":"SWEEP_CONFIG_ERROR","issue_number":42}'
export GH_ISSUE_STATE=open
run_driver
assert_eq "$RC" 1 "21. object-tracked incident is still RED (1)"
assert_contains "$OUT" "already tracked by open issue #42" "21. the object is trusted over the search"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "21. no duplicate issue is filed"

# ── 22. 412 + R2 object with a CLOSED issue_number → re-files ───────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
export STUB_GET_BODY='{"kind":"SWEEP_CONFIG_ERROR","issue_number":42}'
export GH_ISSUE_STATE=closed
run_driver
assert_eq "$RC" 1 "22. closed-issue recurrence exits RED (1)"
assert_match "$(cat "$LOG")" "GH POST .*/issues \{" "22. a closed issue RE-FILES (not swallowed)"
assert_match "$(cat "$LOG")" "AWS put-object body=\{\"kind\":\"SWEEP_CONFIG_ERROR\",\"issue_number\":[0-9]" "22. the re-filed issue number is backfilled (body proven)"

# ── 23. WATCHER_DOWN self-heals on watcher EVIDENCE even on no_work (R3) ────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null true 1)"
export STUB_SWEEP_BODY='{"status":"no_work","teams_backed_up":0}'
export GH_ISSUE_WATCHER_DOWN=66
run_driver
assert_eq "$RC" 1 "23. no_work is still RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "23. no_work files SWEEP_NO_COVERAGE"
assert_match "$(cat "$LOG")" "GH PATCH .*/issues/66" "23. a fresh heartbeat still clears WATCHER_DOWN"

# ── 24. purge ride-along failure → RED ──────────────────────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export STUB_PURGE_CODE=500
run_driver
assert_eq "$RC" 1 "24. purge failure exits RED (1)"
assert_contains "$OUT" "purge ride-along failed" "24. the failure names the purge leg"

# ── 25. reconcile ride-along failure → RED ──────────────────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export STUB_RECONCILE_CODE=500
run_driver
assert_eq "$RC" 1 "25. reconcile failure exits RED (1)"
assert_contains "$OUT" "reconcile ride-along failed" "25. the failure names the reconcile leg"

# ── 26. bare/unquoted secret runs are redacted too (security review) ────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false "load_config failed: ghp_16C7e42F292c6912E7710c838347Ae178B4a at /home/runner/app/x.py" null)"
run_driver
assert_eq "$RC" 1 "26. bare-run secret + broken config exits RED (1)"
assert_not_contains "$(cat "$LOG")" "ghp_16C7e42F292c6912E7710c838347Ae178B4a" "26. an unquoted PAT is NOT published"
assert_not_contains "$OUT" "ghp_16C7e42F292c6912E7710c838347Ae178B4a" "26. an unquoted PAT is NOT logged"

# ── 27. last_sweep error text is redacted before publication ────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(printf '{"enabled":true,"config_error":null,"storage_error":null,"per_team":{},"last_sweep":{"last_sweep_at":"%s","last_team_count":1,"graph_failures":[{"error":"boom ghp_16C7e42F292c6912E7710c838347Ae178B4a"}]},"watcher":{"running":true,"age_minutes":1}}' "$TS_RECENT")"
export STUB_SWEEP_BODY='{"status":"no_work","teams_backed_up":0}'
run_driver
assert_eq "$RC" 1 "27. 0-team + pool data exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "27. no-coverage is filed"
assert_not_contains "$(cat "$LOG")" "ghp_16C7e42F292c6912E7710c838347Ae178B4a" "27. last_sweep error text is NOT published"

# ── 28. a 404 (deleted) tracked issue re-files; a 500 blip does not duplicate
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
export STUB_GET_BODY='{"kind":"SWEEP_CONFIG_ERROR","issue_number":42}'
export STUB_ISSUE_CODE=404
run_driver
assert_eq "$RC" 1 "28a. 404 tracked issue is still RED (1)"
assert_match "$(cat "$LOG")" "GH POST .*/issues \{" "28a. a deleted issue re-files the recurrence"
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
export STUB_GET_BODY='{"kind":"SWEEP_CONFIG_ERROR","issue_number":42}'
export STUB_ISSUE_CODE=500
run_driver
assert_eq "$RC" 1 "28b. transient 500 exits RED (1)"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "28b. a transient 500 assumes open (no duplicate)"

# ── 29. resolve deletes BOTH spellings: canonical _.json + legacy global.json ─
reset_case
# The driver's post-#2844 sentinel is the canonical spelling…
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export GH_ISSUE_APP_DOWN=99
run_driver
assert_eq "$RC" 0 "29. healthy run exits 0"
assert_match "$(cat "$LOG")" "AWS delete-object key=ops/alerts/APP_DOWN/_.json" "29. the driver dedup object is deleted on resolve"
assert_match "$(cat "$LOG")" "AWS delete-object key=ops/alerts/APP_DOWN/global.json" "29. the legacy spelling is deleted too (#2844)"

# ── 29b. a FAILED close keeps the dedup object (cycle-2 review P1) ─────────
# The shell twin of the Python P0: deleting the object on a failed PATCH leaves
# the object gone while the issue stays OPEN, so the next run re-creates it,
# adopts the still-open issue and pages again — while the run reports green.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export GH_ISSUE_APP_DOWN=99
export STUB_GH_CLOSE_CODE=500
run_driver
assert_eq "$RC" 1 "29b. a failed self-heal close exits RED (1), not a silent green"
assert_match "$OUT" "closing issue #99 returned HTTP 500" "29b. the failed close is LOUD"
assert_not_match "$(cat "$LOG")" "AWS delete-object key=ops/alerts/APP_DOWN/global.json" "29b. the driver dedup object is KEPT"
assert_not_match "$(cat "$LOG")" "AWS delete-object key=ops/alerts/APP_DOWN/_.json" "29b. the server-side dedup object is KEPT"

# ── 30. missing .watcher block neither files nor self-heals WATCHER_DOWN ────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(printf '{"enabled":true,"config_error":null,"storage_error":null,"per_team":{},"last_sweep":{"last_sweep_at":"%s","last_team_count":1},"watcher":{}}' "$TS_RECENT")"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export GH_ISSUE_WATCHER_DOWN=66
run_driver
assert_eq "$RC" 0 "30. healthy sweep with an unclassifiable watcher exits 0"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues .*WATCHER_DOWN" "30. no WATCHER_DOWN is filed from a missing block"
assert_not_match "$(cat "$LOG")" "GH PATCH .*/issues/66" "30. a missing block does NOT close WATCHER_DOWN"

# ── 31. degraded is healthy; no_eligible_teams is the no-coverage family ────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"degraded","teams_backed_up":1}'
run_driver
assert_eq "$RC" 0 "31a. degraded exits 0"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues .*SWEEP_NO_COVERAGE" "31a. degraded files no no-coverage"
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_eligible_teams","teams_backed_up":0}'
run_driver
assert_eq "$RC" 1 "31b. no_eligible_teams + pool data exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "31b. no_eligible_teams files SWEEP_NO_COVERAGE"

# ── 32. lock held with no usable last_sweep + pool data → RED ───────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY='{"enabled":true,"config_error":null,"storage_error":null,"per_team":{},"last_sweep":null,"watcher":{"running":true,"age_minutes":1}}'
export STUB_SWEEP_BODY='{"status":"already_running"}'
run_driver
assert_eq "$RC" 1 "32. unverifiable lock + pool data exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "32. unverifiable lock files SWEEP_NO_COVERAGE"

# ── 33. sweep error / non-JSON body → SWEEP_NO_COVERAGE + RED ───────────────
reset_case
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"error"}'
run_driver
assert_eq "$RC" 1 "33a. sweep error exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "33a. sweep error files SWEEP_NO_COVERAGE"
reset_case
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='not json at all'
run_driver
assert_eq "$RC" 1 "33b. non-JSON sweep body exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "33b. non-JSON sweep body files SWEEP_NO_COVERAGE"

# ── 34. missing GITHUB_TOKEN → fail closed ─────────────────────────────────
reset_case
export STUB_STATUS_BODY="$(status_body true null null)"
unset GITHUB_TOKEN
run_driver
assert_eq "$RC" 1 "34. missing GITHUB_TOKEN exits RED (1)"
assert_contains "$OUT" "GITHUB_TOKEN not set" "34. the driver refuses to run blind"
export GITHUB_TOKEN="test-token"

# ── 35. a per-team listing failure is unmeasured, not a false stale ────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export STUB_LIST_FAIL_TEAM=1
export STUB_STATUS_BODY="$(status_body false null null)"
run_driver
assert_eq "$RC" 1 "35. per-team listing failure while OFF exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_OFF_STALE "35. an unmeasured team is loud (not a false stale)"
assert_not_contains "$OUT" "no default archive" "35. a failed read is not reported as a missing archive"

# ── 36. positive self-heals (recovered config + measured-fresh pool) ───────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false null null)"
export GH_ISSUE_SWEEP_CONFIG_ERROR=44
export GH_ISSUE_SWEEP_OFF_STALE=45
run_driver
assert_eq "$RC" 0 "36. recovered config + measured-fresh pool exits 0"
assert_match "$(cat "$LOG")" "GH PATCH .*/issues/44" "36. SWEEP_CONFIG_ERROR self-heals when the config is readable"
assert_match "$(cat "$LOG")" "GH PATCH .*/issues/45" "36. SWEEP_OFF_STALE self-heals on a measured-fresh pool"

# ── 37. multi-team pool: `--output text` is ONE tab-separated line (P1) ────
# Review P1 (bug-deep, conf 98): aws renders a list as a single tab-separated
# line, so a bare `while read` measured only the LAST team. teamZ must still be
# seen — its stale default drives POOL_STALE / SWEEP_OFF_STALE.
# Review R1 (test-integrity, conf 95): the ORIGINAL case put the stale team
# LAST, so the ablated driver measured exactly the team the assertion looked
# for and the suite stayed green (ablation-proven). The stale team is now
# FIRST and the last team is FRESH, so only the tab-split can see it.
reset_case
export R2_TEAMS=$'backups/teamZ/\tbackups/teamA/'
export R2_DEFAULT_LIST_Z=""            # teamZ (first): prefix present, no default archive → stale
export R2_DEFAULT_LIST_A="$TS_RECENT"  # teamA (last): fresh — a bare `read` sees only this
export STUB_STATUS_BODY="$(status_body false null null)"
run_driver
assert_eq "$RC" 1 "37. a 2-team tab-separated pool while OFF exits RED (1)"
assert_match "$OUT" "team teamZ: team prefix present but no default archive" "37. the NON-LAST team is measured too"
assert_filed "$(cat "$LOG")" SWEEP_OFF_STALE "37. the non-last stale team drives SWEEP_OFF_STALE"

# ── 38. held lock + UNMEASURED pool → SWEEP_NO_COVERAGE (review P1) ────────
reset_case
export STUB_LIST_FAIL=1
export STUB_STATUS_BODY='{"enabled":true,"config_error":null,"storage_error":null,"per_team":{},"last_sweep":null,"watcher":{"running":true,"age_minutes":1}}'
export STUB_SWEEP_BODY='{"status":"already_running"}'
run_driver
assert_eq "$RC" 1 "38. stuck lock + unmeasurable pool exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "38. unknown pool is never read as empty for a held lock"
assert_not_contains "$OUT" "leaving silent" "38. the unmeasurable lock is never silently dropped"

# ── 39. redaction shapes (security review F1; strengthened review R1) ─────
# Review R1 (test-integrity, conf 90): the original values were all ≥20 chars,
# so the generic ≥20-char rule masked them and 7 of the 9 shape rules could be
# deleted with the suite still green. Every value below is the shortest its own
# shape rule can match (a sub-20 secret for rules 1–6, a 20/21-char value for
# rules 7/8), so deleting the owning rule turns the case red.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"dsn docker://:pwd12345@host:6379 schemeless user:pwdXYZ789@host hdr Authorization: Bearer tokEN123 pat ghp_AB12cd34 assign MY_SECRET_KEY=shrt999 quoted (got '\''qZwXeDcR'\'') long \"AbCdEfGhIjKlMnOpQrSt\" bare BareTokenZz0123456789X qkey \"api_key\":\"shrtpw1\" hkey \"x-api-key\":\"hyphenpw1\" dkey \"client-secret\":\"dotpw1\""' null)"
run_driver
assert_eq "$RC" 1 "39. secret-bearing config error exits RED (1)"
# one short value per shape rule (1 URI, 2 schemeless, 3 header, 4 prefix,
# 5 assignment, 6 single-quoted, 7 quoted ≥20, 8 bare ≥20, 5b quoted-key
# underscore, 5b quoted-key hyphen, 5b quoted-key dot)
for leaked in pwd12345 pwdXYZ789 tokEN123 AB12cd34 shrt999 qZwXeDcR AbCdEfGhIjKlMnOpQrSt BareTokenZz0123456789X shrtpw1 hyphenpw1 dotpw1; do
  assert_not_contains "$(cat "$LOG")" "$leaked" "39. the '$leaked' shape is NOT published"
  assert_not_contains "$OUT" "$leaked" "39. the '$leaked' shape is NOT logged"
done
assert_match "$OUT" "<redacted>" "39. the redaction marker is present"

# ── 40. the purge failure body is redacted ────────────────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export STUB_PURGE_CODE=500
export STUB_PURGE_BODY='{"status":"error","detail":"mirror misconfig with key TESTONLYTOKENAbCdEfGhIjKlMnOpQrSt"}'
run_driver
assert_eq "$RC" 1 "40. a purge failure is RED (1)"
assert_not_contains "$OUT" "TESTONLYTOKENAbCdEfGhIjKlMnOpQrSt" "40. the purge body secret is NOT published"
assert_match "$OUT" "<redacted>" "40. the purge failure body is redacted in the log"

# ── 41. storage_error while ENABLED is loud and blocks R2_DOWN self-heal ───
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null '"heartbeat read: boom"')"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export GH_ISSUE_R2_DOWN=88
run_driver
assert_eq "$RC" 1 "41. storage_error while enabled exits RED (1)"
assert_match "$(cat "$LOG")" "AWS put-object key=ops/alerts/R2_DOWN/_.json" "41. storage_error while enabled records R2_DOWN"
assert_not_match "$(cat "$LOG")" "GH PATCH .*/issues/88" "41. R2_DOWN is NOT closed while storage_error is set"

# ── 42. no_work with graph_totals.backed_up>0 is not a coverage gap ───────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_work","teams_backed_up":0,"graph_totals":{"attempted":2,"backed_up":1,"failed":1}}'
run_driver
assert_eq "$RC" 0 "42. a custom-graph backup is not a 0-coverage outage"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues .*SWEEP_NO_COVERAGE" "42. no false SWEEP_NO_COVERAGE"
# Review R1 (P2): this run HAS coverage, so an open SWEEP_NO_COVERAGE must
# close here — otherwise a recovered pipeline keeps a stale incident forever.
export GH_ISSUE_SWEEP_NO_COVERAGE=123
run_driver
assert_eq "$RC" 0 "42. (open incident) a custom-graph backup still exits 0"
assert_match "$(cat "$LOG")" "GH PATCH .*/issues/123" "42. SWEEP_NO_COVERAGE self-heals on a covered no_work run"

# ── 43. APP_DOWN is RED (any filing is RED) ──────────────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export SIMULATE_APP_DOWN=true
run_driver
assert_eq "$RC" 1 "43. APP_DOWN exits RED (1)"
assert_filed "$(cat "$LOG")" APP_DOWN "43. APP_DOWN is filed"

# ── 44. an index read failure is unmeasured, not "no index" ──────────────
# STUB_INDEX_FAIL makes the classification-index get-object fail with a
# non-404 error while the legacy-flat listing succeeds.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_FLAT_LIST='[["backups/teamA/2024/dump.enc","2024-01-01T00:00:00Z"]]'
export STUB_INDEX_FAIL=1
export STUB_STATUS_BODY="$(status_body false null null)"
run_driver
assert_eq "$RC" 1 "44. an unreadable flat index while OFF exits RED (1)"
assert_contains "$OUT" "legacy-flat index read FAILED" "44. the failed index read is surfaced"
assert_filed "$(cat "$LOG")" SWEEP_OFF_STALE "44. unreadable index is unmeasured → no confirmed pause"

# ── 45. a measured-fresh off pool resolves a stale R2_DOWN ────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false null null)"
export GH_ISSUE_R2_DOWN=88
run_driver
assert_eq "$RC" 0 "45. recovered storage exits 0"
assert_match "$(cat "$LOG")" "GH PATCH .*/issues/88" "45. R2_DOWN self-heals once storage answers while OFF"

# ── 46. SWEEP_NO_COVERAGE self-heals on a degraded/backed_up sweep ────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"degraded","teams_backed_up":1,"graph_totals":{"backed_up":1}}'
export GH_ISSUE_SWEEP_NO_COVERAGE=123
run_driver
assert_eq "$RC" 0 "46. a healthy degraded sweep exits 0"
assert_match "$(cat "$LOG")" "GH PATCH .*/issues/123" "46. SWEEP_NO_COVERAGE self-heals on a degraded sweep"

# ── 47. a per-team STALE while ENABLED also makes the run RED ─────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$(python3 -c "import datetime;print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=500)).strftime('%Y-%m-%dT%H:%M:%SZ'))")"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
run_driver
assert_eq "$RC" 1 "47. an enabled run with a stale team exits RED (1)"
assert_filed "$(cat "$LOG")" STALE "47. the per-team STALE is filed"

# ── 48. enum_failed is the catch-all family → SWEEP_NO_COVERAGE + RED ─────
reset_case
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"enum_failed","error":"boom","teams_backed_up":0}'
run_driver
assert_eq "$RC" 1 "48. enum_failed exits RED (1)"
assert_filed "$(cat "$LOG")" SWEEP_NO_COVERAGE "48. enum_failed files SWEEP_NO_COVERAGE"

# ── 49. no_eligible_teams + measured-EMPTY pool → silent ─────────────────
reset_case
export R2_TEAMS=""
export R2_DEFAULT_LIST=""
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_eligible_teams","teams_backed_up":0}'
run_driver
assert_eq "$RC" 0 "49. no_eligible_teams + empty pool exits 0 (pre-beta)"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues .*SWEEP_NO_COVERAGE" "49. no false SWEEP_NO_COVERAGE"

# ── 50. redaction shapes + false-positive guards ─────────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"boom ghp_ABCDEFGH\nIJKLMNOPQRSTUVWXYZ0123456789 dsn=user:SuperSecret123@db.internal:6379 redis://:EmptyUserPw123@db:6379 falkor://user:p/ssw0rd@db:6379 compatible: true patch: 3 author: bob password='\''x sekritTail1'\'' key=\"y sekritTail2\" PGPASSWORD=pgPw9 dbpassword=dbPw9 accessToken=accTok9 passwd=\"unterm sekritTail6 secret='\''unterm sekritTail7"' null)"
run_driver
assert_eq "$RC" 1 "50. a secret-bearing config error exits RED (1)"
for leaked in SuperSecret123 ghp_ABCDEFGH EmptyUserPw123; do
  assert_not_contains "$OUT" "$leaked" "50. '$leaked' is NOT logged"
done
assert_not_contains "$OUT" "ssw0rd" "50. a password containing '/' does not leak its tail"
assert_not_contains "$OUT" "IJKLMNOPQRSTUVWXYZ0123456789" "50. the newline-split token fragment is redacted"
assert_contains "$OUT" "compatible: true" "50. 'compatible:' is not a false positive"
assert_contains "$OUT" "patch: 3" "50. 'patch:' is not a false positive"
assert_contains "$OUT" "author: bob" "50. 'author:' is not a false positive"
# Review R4 (P3): a quoted value containing whitespace must be consumed whole
# (the old rule-5 value class stopped at the first space and left the tail).
assert_not_contains "$OUT" "sekritTail1" "50. a single-quoted value with spaces leaves no tail"
assert_not_contains "$OUT" "sekritTail2" "50. a double-quoted value with spaces leaves no tail"
# Review R5 (P2 regression): an UNTERMINATED quoted value must still redact —
# the balanced-quote value class alone failed the whole rule and leaked.
assert_not_contains "$OUT" "sekritTail6" "50. an unterminated double-quoted value is redacted"
assert_not_contains "$OUT" "sekritTail7" "50. an unterminated single-quoted value is redacted"
# Review R5 (P3 regression): separator-less credential keys must still match
# (the word-segment boundary dropped PGPASSWORD/dbpassword/accessToken).
assert_not_contains "$OUT" "pgPw9" "50. a PGPASSWORD-style key is redacted"
assert_not_contains "$OUT" "dbPw9" "50. a concatenated lowercase key is redacted"
assert_not_contains "$OUT" "accTok9" "50. a camelCase key is redacted"

# ── 51. the success branch backfills the R2 object with its issue_number ─
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
run_driver
assert_eq "$RC" 1 "51. a fresh config incident exits RED (1)"
assert_match "$(cat "$LOG")" "GH POST .*/issues \{" "51. the incident is created"
assert_match "$(cat "$LOG")" "AWS put-object body=\{\"kind\":\"SWEEP_CONFIG_ERROR\",\"issue_number\":[0-9]" "51. the object is rewritten with a real issue number"

# ── 52. a failed listing blocks the R2_DOWN self-heal (F2) ──────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_LIST_FAIL=1
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export GH_ISSUE_R2_DOWN=88
run_driver
assert_eq "$RC" 1 "52. a backed_up sweep with a failed listing exits RED (1)"
assert_not_match "$(cat "$LOG")" "GH PATCH .*/issues/88" "52. R2_DOWN is NOT closed while the pool is unmeasured"

# ── 53. a failed legacy-flat listing is unmeasured ──────────────────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_FLAT_LIST='[["backups/teamA/2024/dump.enc","2024-01-01T00:00:00Z"]]'
export STUB_FLAT_FAIL=1
export STUB_STATUS_BODY="$(status_body false null null)"
run_driver
assert_eq "$RC" 1 "53. a failed flat listing while OFF exits RED (1)"
assert_match "$OUT" "legacy-flat listing FAILED" "53. the failed flat listing is surfaced"

# ── 54. an unparseable archive timestamp is unmeasurable → loud ─────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="not-a-timestamp"
export STUB_STATUS_BODY="$(status_body false null null)"
run_driver
assert_eq "$RC" 1 "54. an unparseable archive timestamp while OFF exits RED (1)"
assert_match "$OUT" "unparseable archive timestamp" "54. the unparseable timestamp is surfaced"
assert_filed "$(cat "$LOG")" SWEEP_OFF_STALE "54. an unparseable timestamp is never read as fresh"

# ── 55. a stale watcher AGE alone (running=true) is WATCHER_DOWN ────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null true 999)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
run_driver
assert_eq "$RC" 1 "55. a stale watcher AGE alone exits RED (1)"
assert_match "$(cat "$LOG")" 'GH POST .*/issues .*WATCHER_DOWN' "55. running=true but age>30 files WATCHER_DOWN"

# ── 56. lock + no last_sweep + measured-EMPTY pool is the silent branch ─
reset_case
export R2_TEAMS=""
export STUB_STATUS_BODY='{"enabled":true,"config_error":null,"storage_error":null,"per_team":{},"last_sweep":null,"watcher":{"running":true,"age_minutes":1}}'
export STUB_SWEEP_BODY='{"status":"already_running"}'
run_driver
assert_eq "$RC" 0 "56. a lock with a measured-empty pool exits 0"
assert_contains "$OUT" "leaving silent" "56. the measured-empty lock is the silent branch"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues .*SWEEP_NO_COVERAGE" "56. no incident for a measured-empty lock"

# ── 57. an empty default prefix is measured-empty, not a storage outage ───
# #3659: S3 omits `Contents` on an empty listing, so `sort_by()` raised and the
# CLI exited non-zero — an EMPTY prefix set the GLOBAL R2_LIST_OK=0 and filed a
# platform-wide R2_DOWN while the top-level listing had succeeded. It must be
# a measured result instead: no R2_DOWN, and the honest "prefix present but no
# default archive" branch (which exists for exactly this) is reached.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST=""          # prefix exists, zero objects under default/
export R2_FLAT_LIST="[]"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
run_driver
assert_eq "$RC" 0 "57. an empty default prefix is a healthy measured pool (exit 0)"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues .*R2_DOWN" "57. an empty prefix files NO R2_DOWN"
assert_not_contains "$OUT" "storage is only partially reachable" "57. the pool is not called partially reachable"
assert_contains "$OUT" "team teamA: team prefix present but no default archive" "57. the measured-empty branch is reached"

# ── 58. a failed default call still consumes the legacy-flat leg ──────────
# #3659 defect 2: the old `team_measured=0; continue` bailed out BEFORE
# consuming flat_list, so a pre-#2313 legacy-flat default archive was never
# measured. A genuine per-org default-listing failure must not swallow the
# leg that answered.
reset_case
export R2_TEAMS=$'backups/teamA/'
export STUB_LIST_FAIL_TEAM=1        # the default-archive listing CALL fails
export R2_FLAT_LIST='[["backups/teamA/2024/dump.enc","2024-01-01T00:00:00Z"]]'
export STUB_STATUS_BODY="$(status_body false null null)"
run_driver
assert_eq "$RC" 1 "58. a failed default call with a measured flat leg while OFF exits RED (1)"
assert_contains "$OUT" "filing STALE (direct leg)" "58. the legacy-flat archive is measured (direct-leg STALE)"
assert_filed "$(cat "$LOG")" "STALE — teamA" "58. the legacy-flat archive files the per-team STALE"
assert_contains "$OUT" "default-archive listing FAILED" "58. the genuine call failure is surfaced"

# ── 59. a genuine listing failure surfaces the CLI stderr ────────────────
# #3659 defect 4: both listings redirected stderr to /dev/null, so the run log
# could not distinguish an empty prefix from an AccessDenied. The failure must
# still be a real R2_DOWN AND its cause must be visible.
reset_case
export R2_TEAMS=$'backups/teamA/'
export STUB_LIST_FAIL_TEAM=1
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
run_driver
assert_eq "$RC" 1 "59. a genuine failed listing exits RED (1)"
assert_filed "$(cat "$LOG")" R2_DOWN "59. a genuine call failure is still a real R2_DOWN"
assert_contains "$OUT" "AccessDenied" "59. the CLI stderr is captured, not discarded"

# ── 60. an empty top-level pool is measured-EMPTY, not "None" ──────────
# #3659: an absent CommonPrefixes renders as JSON `null` (under `--output
# text` it was the literal "None", indistinguishable from a team id).
reset_case
export R2_TEAMS=""              # empty pool → CommonPrefixes absent → JSON null
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_teams","teams_backed_up":0}'
run_driver
assert_eq "$RC" 0 "60. an empty pool (JSON null) is a measured-empty exit 0"
assert_not_contains "$OUT" "team None" "60. no fabricated 'None' team is ever measured"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues .*SWEEP_NO_COVERAGE" "60. no false coverage incident on an empty pool"
# A team genuinely named "None" must survive the JSON decode (it must not be
# conflated with the null-rendering artifact).
reset_case
export R2_TEAMS=$'backups/None/'
export R2_DEFAULT_LIST="$TS_STALE"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
run_driver
assert_eq "$RC" 1 "60b. a stale team named 'None' is measured and RED (1)"
assert_contains "$OUT" "team None: newest archive" "60b. the 'None' team is measured, not dropped"

# ── 61. an empty legacy-flat prefix renders as JSON `null`, not `[]` ─────
# botocore returns the literal `null` for an absent Contents; the driver must
# normalize it to the empty list, otherwise it routes into flat classification
# and an index-read error blanks a MEASURED default archive and files a false
# R2_DOWN.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"   # default leg MEASURED fresh
export R2_FLAT_LIST=""                # empty flat prefix → CLI renders `null`
export STUB_INDEX_FAIL=1              # would fail IF the driver mis-read the null
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
run_driver
assert_eq "$RC" 0 "61. a null flat listing with a measured default is healthy (exit 0)"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues .*R2_DOWN" "61. no false R2_DOWN from a null flat listing"
assert_not_contains "$OUT" "legacy-flat index read FAILED" "61. the null flat listing never enters flat classification"

# ── 62. an unparseable top-level listing is UNKNOWN, not an empty pool ──
# An exit-0 body the decoder cannot read must not collapse to a measured-empty
# pool (which would suppress every downstream freshness/coverage signal).
reset_case
export R2_TEAMS=$'backups/teamA/'
export STUB_TOP_NOT_JSON=1             # exit-0 body the driver's jq cannot read
export R2_DEFAULT_LIST="$TS_STALE"     # a stale archive that must not be ignored
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
run_driver
assert_eq "$RC" 1 "62. an unparseable top-level listing exits RED (1)"
assert_contains "$OUT" "R2 top-level listing UNPARSEABLE" "62. the unparseable listing is surfaced"
assert_filed "$(cat "$LOG")" R2_DOWN "62. unknown is never read as a measured-empty pool"

# ── 63. the top-level listing stderr is captured, not discarded ──────────
reset_case
export STUB_LIST_FAIL=1
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_teams","teams_backed_up":0}'
run_driver
assert_eq "$RC" 1 "63. a failed top-level listing exits RED (1)"
assert_contains "$OUT" "AccessDenied" "63. the top-level CLI stderr is captured, not discarded"

# ── 64. #3029: a FAILED search is not "no incident" — defer, never duplicate ─
# The alerter's dedup authority is the create-once object; the search is the
# fallback that decides whether an issue already exists. When the search does
# not run, treating it as empty files a DUPLICATE — so filing is deferred (the
# placeholder object keeps issue_number:null, so the next run's 412 branch
# retries) and the run goes RED rather than silent.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_GH_SEARCH_FAIL=1
run_driver
assert_eq "$RC" 1 "64. a failed search exits RED (1)"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "64. a failed search never files a duplicate"
assert_contains "$OUT" "filing DEFERRED" "64. the deferral is loud and explicit"
assert_match "$(cat "$LOG")" "AWS put-object key=ops/alerts/SWEEP_CONFIG_ERROR" "64. the dedup object is still created (the next run retries)"

# ── 65. #3029: an ERROR BODY (403 rate-limit) is not an empty result ─────────
# GitHub answers a rate-limited search with HTTP 200-ish JSON that carries no
# `items`, and curl exits 0 — the pre-#3029 `.items[0] // empty` read that as
# "no open incident".
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_GH_SEARCH_BODY='{"message":"API rate limit exceeded"}'
run_driver
assert_eq "$RC" 1 "65. a rate-limit body exits RED (1)"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "65. a rate-limit body is not read as 'no incident'"
assert_contains "$OUT" "filing DEFERRED" "65. the rate-limit body is treated as a search failure"

# ── 66. #3029: an issue that merely MENTIONS the kind is never adopted ───────
# The live production shape: the platform-scoped query for `[DR] R2_DOWN`
# resolved to #2844 — a bug report ABOUT R2_DOWN (title "bug(dr): R2_DOWN is
# deduped under two different R2 keys…"). Adoption would have closed it.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_GH_SEARCH_BODY='{"items":[{"number":2844,"title":"bug(dr): R2_DOWN is deduped under two different R2 keys by the driver and the app watcher"},{"number":2845,"title":"bug(dr): SWEEP_CONFIG_ERROR mentions this kind in prose"}]}'
run_driver
assert_eq "$RC" 1 "66. a prose mention does not suppress filing (still RED)"
assert_filed "$(cat "$LOG")" "SWEEP_CONFIG_ERROR" "66. the driver files its OWN issue instead of adopting #2844"
assert_not_match "$(cat "$LOG")" "issues/2844" "66. the unrelated issue is never touched (not closed, not commented)"

# ── 67. #3029: the kind must follow `[DR] ` immediately (boundary) ───────────
# `[DR] SWEEP_CONFIG_ERROR_EXTRA` must not be adopted for kind
# SWEEP_CONFIG_ERROR — a bare startswith would accept it.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_GH_SEARCH_BODY='{"items":[{"number":777,"title":"[DR] SWEEP_CONFIG_ERROR_EXTRA — a different kind"},{"number":778,"title":"[DR] SWEEP_CONFIG_ERRORish"}]}'
run_driver
assert_filed "$(cat "$LOG")" "SWEEP_CONFIG_ERROR" "67. a kind-prefix collision is not adopted"
assert_not_match "$(cat "$LOG")" "issues/777" "67. the colliding kind's issue is untouched"

# ── 68. #3029: the subject must be the EXACT ` — ` segment ────────────────
# A bare team subject is a literal PREFIX of its per-graph subjects, so
# `[DR] STALE — teamA:g_x` must never satisfy a search for subject teamA —
# adopting it would let teamA's recovery close the custom graph's live issue
# (#2413/#2375 cross-talk).
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_STALE"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export STUB_GH_SEARCH_BODY='{"items":[{"number":555,"title":"[DR] STALE — teamA:g_x — last backup 3h"}]}'
run_driver
assert_filed "$(cat "$LOG")" STALE "68. a per-graph subject does not satisfy the team subject"
assert_not_match "$(cat "$LOG")" "issues/555" "68. the per-graph issue is not adopted by the team incident"

# ── 69. #3032: unsupported IfNoneMatch + ambiguous HEAD → LOUD, never a blind put
# The Python twin RAISES in this case (`hosted_backup.create_if_not_exists`:
# "a blind-put would weaken the dedup linearization point"); the shell twin must
# not create an object it cannot prove absent — that could overwrite a
# concurrent writer's object and null its issue_number.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_NO_IFNONEMATCH=1
export STUB_HEAD_EXISTS=0
run_driver
assert_eq "$RC" 1 "69. an ambiguous dedup write exits RED (1)"
assert_match "$(cat "$LOG")" "AWS head-object key=ops/alerts/SWEEP_CONFIG_ERROR" "69. the fallback HEAD-checks the object"
assert_contains "$OUT" "refusing a blind put" "69. an unconfirmable object is never blind-put"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "69. nothing is filed on an unverified dedup"

# ── 69b. #3032: a bare `412` in an unrelated error is NOT "already exists" ──
# The pre-review glob `*412*` matched any request-id/byte-count, reporting
# "exists" without ever HEAD-checking. Only the real AWS markers may shortcut.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_PUT_412_SUBSTRING=1
export STUB_HEAD_EXISTS=1
export STUB_GET_BODY='{"kind":"SWEEP_CONFIG_ERROR","issue_number":42}'
# The alias pre-read (main's #2844 `file_alert`) would otherwise adopt the
# object from the legacy `global.json` spelling and never reach the create-once
# write this case is guarding.
export STUB_GET_ONLY_CANONICAL=1
run_driver
assert_match "$(cat "$LOG")" "AWS head-object key=ops/alerts/SWEEP_CONFIG_ERROR" "69b. an unrelated 412 substring still HEAD-checks"
assert_contains "$OUT" "already tracked by open issue #42" "69b. the existing object is adopted (not a false exists)"

# ── 70. #3032: unsupported IfNoneMatch + EXISTING object → adopt, no duplicate
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_NO_IFNONEMATCH=1
export STUB_HEAD_EXISTS=1
export STUB_GET_BODY='{"kind":"SWEEP_CONFIG_ERROR","issue_number":42}'
export GH_ISSUE_SWEEP_CONFIG_ERROR=42
run_driver
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "70. an existing object is adopted (no duplicate filed)"
assert_contains "$OUT" "already tracked by open issue #42" "70. the open issue is adopted from the object"

# ── 71. #3032: an UNRESOLVED dedup write fails LOUD, never search-only ──────
# Conditional write rejected AND HEAD cannot confirm the object AND the
# unconditional put fails: dedup is unverifiable, so the run must not proceed
# on the fail-open search.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_PUT_FAIL=1
run_driver
assert_eq "$RC" 1 "71. an unresolved dedup write exits RED (1)"
assert_contains "$OUT" "Dedup is unverified" "71. the failure is loud and names the cause"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "71. nothing is filed on an unverified dedup"

# ── 64. a subject-less incident is written ONCE, under the canonical spelling ─
# #2844: the driver's pre-fix spelling was `global.json` while the server-side
# AlertStore writes `_.json`. Two spellings = two create-once points = two issues
# for one condition, and a resolve that deletes only one strands the other.
reset_case
export STUB_R2_DOWN=1
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_teams","teams_backed_up":0}'
run_driver
assert_match "$(cat "$LOG")" "AWS put-object key=ops/alerts/R2_DOWN/_.json" "64. the canonical (AlertStore) spelling is written"
assert_not_match "$(cat "$LOG")" "AWS put-object key=ops/alerts/R2_DOWN/global.json" "64. the legacy spelling is NOT written (one create-once point)"

# ── 65. a legacy sentinel holding a CLOSED issue does not block re-filing ───
# The other half of #2844: adopting on sight would swallow a recurrence. The
# alias is adopted only while its issue is still OPEN.
reset_case
export STUB_R2_DOWN=1
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"no_teams","teams_backed_up":0}'
export STUB_412=0
export STUB_GET_BODY='{"kind":"R2_DOWN","issue_number":91}'
export GH_ISSUE_STATE=closed
run_driver
assert_match "$(cat "$LOG")" "AWS put-object key=ops/alerts/R2_DOWN/_.json" "65. a closed legacy sentinel still lets the incident re-file"

# ── 66. the driver's ownership refusal is pinned (#3127) ────────────────────
# `resolve_global` must self-heal ONLY kinds the driver owns (KIND_OWNERS). The
# refusal had no test: deleting the whole block kept every other assertion
# green, so a regression could quietly restore driver authority over
# watcher/app kinds — the false recovery #3127 exists to prevent. The full
# driver cannot reach this path (every call site is driver-owned BY CONTRACT,
# pinned by test_kind_owner_contract_with_driver), so the real functions are
# extracted and driven directly with stubbed I/O. Extract from the SHIPPING
# script, never a copy, so deleting the block fails this case.
reset_case
RESOLVE_EXT="$(mktemp)"
RESOLVE_LOG="$(mktemp)"
sed -n '/^GH_SEARCH_FAILED_SENTINEL=/p; /^kind_owner()/,/^}/p; /^resolve_global()/,/^}/p' "$DRIVER" > "$RESOLVE_EXT"
run_resolve() { # kind — real kind_owner/resolve_global + stubbed log/gh/r2
  local kind="$1"
  : > "$RESOLVE_LOG"
  (
    set +e
    log() { printf 'REFUSE %s\n' "$1" >> "$RESOLVE_LOG"; }
    gh_find_open() { printf 'FIND %s\n' "$*" >> "$RESOLVE_LOG"; printf '55'; }
    gh_close() { printf 'CLOSE %s\n' "$*" >> "$RESOLVE_LOG"; }
    # shellcheck disable=SC1090
    . "$RESOLVE_EXT"
    resolve_global "$kind" "Resolved — test."
  ) >/dev/null 2>&1 || true
  cat "$RESOLVE_LOG"
}
assert_contains "$(run_resolve STALE)" "refusing to close" "66. a watcher-owned kind is refused"
assert_not_contains "$(run_resolve STALE)" "FIND" "66. the refusal never searches for an issue"
assert_not_contains "$(run_resolve STALE)" "CLOSE" "66. the refusal never closes one"
assert_contains "$(run_resolve WATCHER_DOWN)" "FIND" "66. a driver-owned kind searches for the incident"
assert_contains "$(run_resolve WATCHER_DOWN)" "CLOSE" "66. a driver-owned kind closes the incident"
assert_not_contains "$(run_resolve WATCHER_DOWN)" "refusing to close" "66. the driver-owned path does not refuse"
rm -f "$RESOLVE_EXT" "$RESOLVE_LOG"

# ── 67. a REAL subject named `global` is not the platform (subject-less) alias
# #2844 round-7 P2: `alert_key` canonicalized a NON-EMPTY subject literally
# named `global` to `_.json`, while AlertStore._keys kept `global.json` for that
# same subject — so a team actually named `global` got two create-once points
# (two sentinels, two issues for one condition). Canonicalization is for the
# EMPTY id only; the legacy `global` spelling stays a READ/DELETE alias of the
# EMPTY id alone. Extracted from the SHIPPING script, never a copy, so
# re-widening either function fails here.
reset_case
KEY_EXT="$(mktemp)"
sed -n '/^alert_key()/,/^}/p; /^alert_keys_all()/,/^}/p' "$DRIVER" > "$KEY_EXT"
run_keys() { # fn id — the real alert_key/alert_keys_all from the shipping driver
  (
    # shellcheck disable=SC1090
    . "$KEY_EXT"
    "$1" STALE "$2"
  )
}
assert_eq "$(run_keys alert_key "")" "ops/alerts/STALE/_.json" \
  "67. the EMPTY (platform) id canonicalizes to _.json"
assert_eq "$(run_keys alert_key global)" "ops/alerts/STALE/global.json" \
  "67. a REAL subject named global keeps its OWN key, not the platform alias"
_keys_empty="$(run_keys alert_keys_all "")"
assert_contains "$_keys_empty" "ops/alerts/STALE/_.json" \
  "67. the platform id lists the canonical spelling"
assert_contains "$_keys_empty" "ops/alerts/STALE/global.json" \
  "67. the platform id still lists the legacy global.json alias (read/delete)"
assert_eq "$(printf '%s\n' "$_keys_empty" | wc -l | tr -d ' ')" "2" \
  "67. the platform id has exactly two spellings"
assert_eq "$(run_keys alert_keys_all global)" "ops/alerts/STALE/global.json" \
  "67. a REAL subject named global is never an alias set"
assert_eq "$(run_keys alert_keys_all _)" "ops/alerts/STALE/_.json" \
  "67. a REAL subject named _ is never an alias set"
rm -f "$KEY_EXT"

# ── 68. a dedup no-op RECORDS the recurrence (#3907) ───────────────────────
# The pre-#3907 dedup branches logged "already tracked … — no-op" and returned,
# so an hourly driver on ONE unchanged fault left NO trace on the issue: the
# finding was filed, but the escalation was invisible. Dedupe that hides the
# escalation converts "noisy" into "blind". The recurrence is now recorded as a
# marked comment, and its NUMBER is read from the issue's own prior occurrence
# comments (not from the R2 object, which the server-side AlertStore rewrites).
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
export STUB_GET_BODY='{"kind":"SWEEP_CONFIG_ERROR","issue_number":42}'
export GH_ISSUE_STATE=open
export STUB_COMMENTS_JSON='[]'
run_driver
assert_eq "$RC" 1 "68. the repeat is still RED (1) — dedupe never silences the run"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "68. the repeat still files NO duplicate issue"
assert_match "$(cat "$LOG")" "GH GET .*/issues/42/comments" "68. the occurrence count is read from the ISSUE"
assert_match "$(cat "$LOG")" "GH POST .*/issues/42/comments .*Recurrence #1" "68. occurrence #1 is recorded on the issue"
assert_match "$(cat "$LOG")" "GH POST .*/issues/42/comments .*dr-occurrence" "68. the comment carries the counting marker"

# ── 69. the recurrence counter ADVANCES from the issue's own comments ───────
# Two marked comments already on #42 → the next record is #3. This pins that
# the number is derived from the durable, visible record, so a re-run (or the
# AlertStore rewriting the shared R2 object) cannot reset it.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
export STUB_GET_BODY='{"kind":"SWEEP_CONFIG_ERROR","issue_number":42}'
export GH_ISSUE_STATE=open
export STUB_COMMENTS_JSON='[{"body":"🔁 Recurrence #1 <!-- dr-occurrence -->","user":{"login":"github-actions[bot]","type":"Bot"}},{"body":"a human comment","user":{"login":"someone","type":"User"}},{"body":"🔁 Recurrence #2 <!-- dr-occurrence -->","user":{"login":"github-actions[bot]","type":"Bot"}}]'
run_driver
assert_eq "$RC" 1 "69. the repeat is still RED (1)"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "69. no duplicate issue is filed"
assert_match "$(cat "$LOG")" "GH POST .*/issues/42/comments .*Recurrence #3" "69. the counter advances to #3 (2 prior + this one)"
assert_eq "$(grep -c 'GH POST .*/issues/42/comments' "$LOG" || true)" "1" "69. exactly ONE occurrence comment is posted per run"

# ── 70. a first-time fault still files; a recovered fault still self-heals ──
# The safety property #3907 must NOT break: the very first incident files (and
# is not mislabelled as a recurrence of itself), and a recovered fault still
# closes its incident and deletes its dedup object so the next occurrence is a
# genuinely new incident.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
run_driver
assert_eq "$RC" 1 "70a. a first-time fault exits RED (1)"
assert_match "$(cat "$LOG")" "GH POST .*/issues \{" "70a. a first-time fault still FILES"
assert_not_match "$(cat "$LOG")" "GH POST .*/comments .*Recurrence" "70a. a first-time fault records no recurrence"
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false null null)"
export GH_ISSUE_SWEEP_CONFIG_ERROR=42
run_driver
assert_eq "$RC" 0 "70b. a recovered config error exits 0"
assert_match "$(cat "$LOG")" "GH PATCH .*/issues/42" "70b. a recovered fault still self-heals (the incident closes)"
assert_match "$(cat "$LOG")" "AWS delete-object key=ops/alerts/SWEEP_CONFIG_ERROR/_.json" "70b. the dedup object is deleted on resolve"

# ── 71. a FAILED recurrence comment is never fatal and never duplicates ─────
# The recording is additive: the run is already RED (LOUD was set before any
# dedup branch), so a failed comment must not abort the self-heal or the other
# incidents, and must never be replaced by a duplicate issue. The driver also
# must not claim a record it did not write.
# The failure is an HTTP 403 answered by the comments endpoint with curl exit 0
# — the shape a real GitHub returns under a secondary rate limit or a missing
# `issues: write` permission. The pre-fix `gh_comment` reported that as success
# (a bare `curl -sS` exits 0 on 4xx/5xx) and the driver logged a recorded
# recurrence the issue never carried.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
export STUB_GET_BODY='{"kind":"SWEEP_CONFIG_ERROR","issue_number":42}'
export GH_ISSUE_STATE=open
export STUB_COMMENTS_JSON='[]'
export STUB_COMMENT_CODE=403
run_driver
assert_eq "$RC" 1 "71. a 403 recurrence comment still exits RED (1)"
assert_match "$(cat "$LOG")" "GH POST .*/issues/42/comments" "71. the comment POST was actually attempted (the 403 path is live)"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "71. a failed comment never produces a duplicate issue"
assert_match "$OUT" "recurrence comment on issue #42 FAILED" "71. the driver does not claim a record it did not write"
assert_not_match "$OUT" "recorded recurrence #1" "71. no false 'recorded' claim on a 403 (exit-0) comment"

# ── 72. an OUTSIDER marker cannot inflate the recurrence counter (P2) ──────
# The occurrence marker is NOT secret: the public comments API returns it
# verbatim, so a bare `contains($m)` let ANY account inflate the number by
# posting the marker. One legitimate bot recurrence plus three outsider
# markers (a human and a foreign bot) once published `Recurrence #5` instead of
# #2 — and the recurrence number is the ONE field of #3907 an outsider can
# corrupt. Only the reserved `github-actions[bot]` login may be counted.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_412=1
export STUB_GET_BODY='{"kind":"SWEEP_CONFIG_ERROR","issue_number":42}'
export GH_ISSUE_STATE=open
export STUB_COMMENTS_JSON='[{"body":"🔁 Recurrence #1 <!-- dr-occurrence -->","user":{"login":"github-actions[bot]","type":"Bot"}},{"body":"<!-- dr-occurrence -->","user":{"login":"attacker","type":"User"}},{"body":"<!-- dr-occurrence -->","user":{"login":"attacker","type":"User"}},{"body":"<!-- dr-occurrence -->","user":{"login":"renovate[bot]","type":"Bot"}}]'
run_driver
assert_eq "$RC" 1 "72. the repeat is still RED (1)"
assert_match "$(cat "$LOG")" "GH POST .*/issues/42/comments .*Recurrence #2" "72. an outsider marker does NOT inflate the counter (1 legit → #2)"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues/42/comments .*Recurrence #5" "72. the P2 over-count direction is closed (incl. a foreign bot)"

# ── 73. a FAILED dedupe search refuses to file (P1) ───────────────────────
# The search API has a separate, LOWER rate limit. A 403/429 answered with
# curl exit 0 previously made `jq -r '.items[0].number // empty'` yield empty,
# which read as "no open issue" → a DUPLICATE (the #2706 direction) inside the
# very guard #3907 exists for. The primitive requires a real 2xx, and the file
# path REFUSES on __ERR__ ("no incident" and "cannot see incidents" are
# different facts).
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_SEARCH_CODE=403
run_driver
assert_eq "$RC" 1 "73. a 403 search exits RED (1)"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "73. a failed search files NO duplicate"
assert_contains "$OUT" "refusing to file a possible duplicate" "73. the refusal says why"
assert_match "$(cat "$LOG")" "AWS put-object key=ops/alerts/SWEEP_CONFIG_ERROR/_.json" "73. the create-once sentinel is kept for the next run"

# ── 74. the dedupe search PAGES to an exact match beyond page 1 (P1) ──────
# GitHub's search ranking is relevance-based, NOT equality-first: a key query
# can fill page 1 with other subjects and leave the EXACT subject on page 2.
# The pre-fix single-page `per_page=20` search returned "none" and filed a
# duplicate; page 2 is only reachable when the walk pages.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$(python3 -c "import datetime;print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=500)).strftime('%Y-%m-%dT%H:%M:%SZ'))")"
export STUB_STATUS_BODY="$(status_body true null null)"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export GH_SEARCH_FILLER=1
export GH_ISSUE_STALE=42
export GH_ISSUE_TITLE='[DR] STALE — teamA'
export GH_ISSUE_STATE=open
export STUB_COMMENTS_JSON='[]'
run_driver
assert_eq "$RC" 1 "74. a page-2 exact match exits RED (1)"
assert_match "$(cat "$LOG")" "GH GET .*search/issues.*page=2" "74. the search walk reached page 2"
assert_not_match "$(cat "$LOG")" "GH POST .*/issues \{" "74. the page-2 exact match files NO duplicate"
assert_match "$(cat "$LOG")" 'AWS put-object body=\{"kind":"STALE","issue_number":42' "74. the page-2 issue number is adopted (only the walk could see it)"

# ── 75. a FAILED issue CREATE files nothing and claims no filing (P2) ─────
# The pre-fix create was a bare curl whose `.number // empty` conflated an HTTP
# failure with "no number": nothing was filed, no line was logged, and
# finish() still printed "an incident was filed — exiting RED".
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false '"REGISTRY_STREAM_KEY not set"' null)"
export STUB_CREATE_CODE=403
run_driver
assert_eq "$RC" 1 "75. a failed create exits RED (1)"
assert_match "$(cat "$LOG")" "GH POST .*/issues \{" "75. the create POST was attempted"
assert_contains "$OUT" "issue CREATE failed" "75. the failed create is logged loudly"
assert_not_contains "$OUT" "an incident was filed" "75. no false claim of a filing that did not happen"
assert_not_match "$(cat "$LOG")" "issue_number\":[0-9]" "75. no issue number is backfilled into the sentinel"

# ── 76. a FAILED close leaves the issue OPEN and KEEPS the sentinel (P2) ──
# The pre-fix close was `curl … >/dev/null 2>&1 || true`: on 403/5xx the issue
# stayed OPEN while the driver believed it resolved AND deleted the R2
# sentinel, so a human saw "unresolved" indefinitely and the next recurrence
# re-adopted a stale issue.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body false null null)"
export GH_ISSUE_SWEEP_CONFIG_ERROR=42
export STUB_CLOSE_CODE=403
run_driver
# The merged runbook (docs/ops/registry-backup-dr.md) states the driver's
# `gh_close` "keeps the dedup object and marks the run red" on a non-2xx PATCH —
# PR #3405's contract, asserted by case 29b. The run is therefore RED even
# though the sweep itself was healthy; the issue is NOT silently green.
assert_eq "$RC" 1 "76. a failed close keeps the sentinel AND marks the run RED (runbook/29b)"
assert_match "$(cat "$LOG")" "GH PATCH .*/issues/42" "76. the close was attempted"
assert_not_match "$(cat "$LOG")" "AWS delete-object key=ops/alerts/SWEEP_CONFIG_ERROR/_.json" "76. the sentinel is KEPT on a failed close"
assert_contains "$OUT" "could NOT close issue #42" "76. the failed close is reported, not swallowed"

# ── 77. #5028: the ride-along skip names the ACTUAL status (lock case) ─────
# The pre-fix else-branch logged "sweep reported already_running (lock held)"
# for ALL THREE skip statuses; driver_timeout and empty_response are not a
# held lock, and that false line sent an investigation after a lock that was
# then falsified (0 of 14 sampled runs reported already_running).
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null true 1 "$TS_RECENT")"
export STUB_SWEEP_BODY='{"status":"already_running"}'
run_driver
assert_eq "$RC" 0 "77. a fresh held lock is healthy"
assert_contains "$OUT" "sweep skipped (status=already_running)" "77. a held lock names already_running"
assert_not_contains "$OUT" "already_running (lock held)" "77. the false lock-held claim is gone"
assert_not_contains "$OUT" "ride-along OK" "77. already_running still skips the purge/reconcile ride-along"

# ── 78. #5028: a sweep timeout names the status AND the elapsed time ───────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null true 1 "$TS_RECENT")"
export STUB_SWEEP_RC=28
run_driver
assert_eq "$RC" 1 "78. a sweep timeout stays RED (1)"
assert_contains "$OUT" "sweep skipped (status=driver_timeout)" "78. a timeout names driver_timeout"
assert_not_contains "$OUT" "lock held" "78. a timeout is NOT reported as a held lock"
assert_match "$OUT" "curl rc=28, took [0-9]+s" "78. the timeout reports rc 28 and the elapsed seconds"
# #5028: the elapsed is carried in the FILED body too, not only the log line —
# the incident is what a human reads weeks later, when the run's stdout is gone.
assert_match "$(cat "$LOG")" 'after [0-9]+s \(curl exit 28' "78. the filed incident body carries the elapsed seconds"
assert_not_contains "$OUT" "ride-along OK" "78. a timeout still skips the purge/reconcile ride-along"

# ── 79. #5028: an empty sweep body names empty_response, not a lock ────────
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(status_body true null null true 1 "$TS_RECENT")"
export STUB_SWEEP_RC=0
run_driver
assert_eq "$RC" 1 "79. an empty response stays RED (1)"
assert_contains "$OUT" "sweep skipped (status=empty_response)" "79. an empty response names empty_response"
assert_not_contains "$OUT" "lock held" "79. an empty response is NOT reported as a held lock"

# ── 80. #5028: the sweep OUTCOME is never truncated away ───────────────────
# `per_team` (the org census) is serialized BEFORE `last_sweep`, so the old
# 600-char `raw status` blob always cut the outcome off the end. The outcome
# must be on its own untruncated line.
reset_case
BIG_PER_TEAM="$(python3 -c 'import json;print(json.dumps({("org%03d" % i):"ok" for i in range(78)}))')"
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_STATUS_BODY="$(printf '{"enabled":true,"config_error":null,"storage_error":null,"per_team":%s,"last_sweep":{"last_sweep_at":"%s","last_team_count":78},"watcher":{"running":true,"age_minutes":1}}' "$BIG_PER_TEAM" "$TS_RECENT")"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
run_driver
assert_eq "$RC" 0 "80. a healthy run with a 78-org census exits 0"
assert_contains "$OUT" "sweep outcome: last_sweep=" "80. the sweep outcome is on its own line"
assert_contains "$OUT" "\"last_sweep_at\":\"$TS_RECENT\"" "80. the outcome line carries last_sweep_at"
assert_contains "$OUT" "orgs=78" "80. the outcome line carries the org census count"
RAW_PREVIEW="$(printf '%s' "$STUB_STATUS_BODY" | head -c 600)"
assert_not_contains "$RAW_PREVIEW" "last_sweep" "80. (control) the 600-char raw blob genuinely cuts last_sweep off"

# ── 81. #5028: the outcome names the org census — `unknown`, never blank ───
# The outcome line must report the real count when `per_team` is an object and
# say `unknown` when it is absent or a non-object. A blank `orgs=` reads as
# "0 orgs" at a glance — the same silent-degradation shape the untruncated
# outcome line exists to prevent. BOTH the jq `else "unknown"` and the
# `[ -n … ] ||` guard are pinned (the else catches a non-object; the guard
# catches the empty output of a jq failure).
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
# (a) `per_team` absent entirely.
export STUB_STATUS_BODY="$(printf '{"enabled":true,"config_error":null,"storage_error":null,"last_sweep":{"last_sweep_at":"%s","last_team_count":0},"watcher":{"running":true,"age_minutes":1}}' "$TS_RECENT")"
run_driver
assert_eq "$RC" 0 "81. a run whose /status omits per_team stays healthy"
assert_contains "$OUT" "sweep outcome: last_sweep=" "81. the outcome line is still emitted"
assert_contains "$OUT" "orgs=unknown" "81. an absent per_team reports orgs=unknown"
assert_not_match "$OUT" "orgs=[0-9]" "81. an absent per_team never reports a numeric count"
# (b) `per_team` present but a non-object (schema drift).
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export STUB_STATUS_BODY="$(printf '{"enabled":true,"config_error":null,"storage_error":null,"per_team":"drifted","last_sweep":{"last_sweep_at":"%s","last_team_count":0},"watcher":{"running":true,"age_minutes":1}}' "$TS_RECENT")"
run_driver
assert_eq "$RC" 0 "81. a run with a non-object per_team stays healthy"
assert_contains "$OUT" "orgs=unknown" "81. a non-object per_team reports orgs=unknown"
# (c) an object `per_team` still reports the real count.
reset_case
export R2_TEAMS=$'backups/teamA/'
export R2_DEFAULT_LIST="$TS_RECENT"
export STUB_SWEEP_BODY='{"status":"backed_up","teams_backed_up":1}'
export STUB_STATUS_BODY="$(printf '{"enabled":true,"config_error":null,"storage_error":null,"per_team":{"orgA":"ok","orgB":"ok"},"last_sweep":{"last_sweep_at":"%s","last_team_count":0},"watcher":{"running":true,"age_minutes":1}}' "$TS_RECENT")"
run_driver
assert_eq "$RC" 0 "81. a run with an object per_team stays healthy"
assert_contains "$OUT" "orgs=2" "81. an object per_team reports the real org count"

echo ""
echo "registry-cron.test.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
