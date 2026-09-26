#!/usr/bin/env bash
# Per-team knowledge-graph backup driver (#596) — the app-down/crash-loop leg.
#
# Pre-flight classification is the critical piece: an OOM crash-loop (#545)
# answers /status between restarts, so the driver's DIRECT R2 freshness check
# (aws CLI) is the only signal that covers the app-down case. The direct-R2
# leg runs BEFORE the APP_DOWN early-exit (review P2-2) — it is genuinely
# app-independent.
#   1. aws preflight (R2_DOWN on failure) + DIRECT R2 freshness (per-team)
#   2. GET /status (classify: connect-fail → APP_DOWN; app-503/429 → up)
#   3. kill-switch check — a DISABLED sweep is NOT automatically intentional:
#      config_error → SWEEP_CONFIG_ERROR; storage_error → R2_DOWN; stale R2
#      pool → SWEEP_OFF_STALE; an unmeasurable pool is never read as fresh.
#      Only a genuinely deliberate pause (no config error, no storage error,
#      a MEASURED fresh pool) stays silent (#2796)
#   4. POST /backups/sweep (a held lock whose last real sweep is older than the
#      driver-down window, or is unverifiable while the pool holds data →
#      SWEEP_NO_COVERAGE)
#   4b. enabled-but-nothing — a sweep that backed up 0 teams while the R2 pool
#      holds team prefixes, or while the pool cannot be measured →
#      SWEEP_NO_COVERAGE, job red (#2796/#2823)
#   5. POST /backups/purge + /reconcile ride-along (skipped when the sweep
#      reported already_running; #2304 purge erases expired trash on the hourly
#      cadence — wired by #2317)
#   6. POST /driver/heartbeat
#   7. self-heal: close an incident only on the evidence that proves it gone —
#      APP_DOWN / resolved SWEEP_CONFIG_ERROR / SWEEP_OFF_STALE on a completed
#      round trip; R2_DOWN on a passing R2 preflight; WATCHER_DOWN on a fresh
#      watcher heartbeat; SWEEP_NO_COVERAGE on a run that actually backed up
set -euo pipefail

API="${INTERNAL_API_URL:-}"
KEY="${FASTAPI_INTERNAL_KEY:-}"
STALE_MIN="${BACKUP_STALE_THRESHOLD_MIN:-90}"
# #2796: the "driver down" window (4 missed hourly runs) is the tripwire for a
# disabled-but-not-deliberate sweep. Same knob the daemon uses
# (tortoise/backup_config.py); already exported by registry-backup-cron.yml.
DRIVER_DOWN_MIN="${BACKUP_DRIVER_DOWN_THRESHOLD_MIN:-240}"
REPO="${GH_REPO:-daniel-ospina/tortoise}"
GH_TOKEN="${GITHUB_TOKEN:-}"
SIMULATE_APP_DOWN="${SIMULATE_APP_DOWN:-false}"
R2_ENDPOINT="https://${R2_ACCOUNT_ID:-}.r2.cloudflarestorage.com"

# aws CLI reads AWS_* env vars — bridge the R2_* names (review fix) and set
# the Cloudflare-required region (SigV4 fails without region=auto on R2).
export AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:-}"
export AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:-}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-auto}"

log() { echo "[backup-driver] $*"; }
fail() { echo "[backup-driver] ERROR: $*" >&2; }
# #2796 (review guidance P2-3): the job must be RED whenever the pipeline is
# broken — i.e. whenever an incident was DETECTED this run, not only on the four
# kill-switch/no-coverage states. `file_alert` sets LOUD and every terminal exit
# goes through finish(). Silent ⟺ nothing was detected this run.
# #3907 review (P2): LOUD records the DETECTION, not a successful filing — a
# failed create keeps the job red while filing nothing — so the message must not
# claim "an incident was filed".
LOUD=0
finish() {
  if [ "${LOUD:-0}" = "1" ]; then
    log "loud run: a broken pipeline was detected — exiting RED"
    exit 1
  fi
  exit 0
}

# ── #2796 (review R2/R4): publication redaction ─────────────────────────────
# The driver PUBLISHES config_error/storage_error/last_sweep into a public
# GitHub issue + Telegram, and app error strings can embed secret material
# (historically `... must be base64 (got '<key prefix>'...)`; the source now
# emits a fingerprint, see tortoise/backup_config.py). Scrub credential SHAPES.
#
# Review round 3: the previous blanket `"[^"]{4,}"` rule redacted every quoted
# JSON KEY (destroying the diagnostic payload and mangling the JSON) while
# still missing DSN/URI passwords, Basic/Bearer headers and short tokens. The
# rules now target credential shapes and preserve lowercase JSON keys/values:
#   1. URI userinfo password     scheme://[user]:PASSWORD@host — matched
#                                greedily to the LAST `@` of the token, so an
#                                empty username (`docker://:pw@host` — this
#                                repo's canonical DSN) and a password
#                                containing `/` or `@` are both covered
#                                (same rule as #720's _mask_uri_userinfo)
#   2. schemeless userinfo       user:PASSWORD@host
#   3. Authorization header      Basic|Bearer|token|ApiKey|OAuth TOKEN
#   4. credential PREFIX         ghp_…, github_pat_…, glpat-…, AKIA… (ANY length,
#                                so a short or line-split PAT cannot survive)
#   5. sensitive-key assignment  *_KEY=value, "api_key":"value", token: value,
#                                password='quoted value', PGPASSWORD=pw, apikey=pw
#                                (suffix-anchored, so `patch:`/`compatible:`/
#                                `author:` are not false positives; the value
#                                class consumes a whole quoted string — balanced
#                                OR unterminated — so a spaced secret cannot
#                                leave an unredacted tail; the key class spans
#                                `-`/`.` for `"x-api-key":"…"` and matches a
#                                separator-less prefix like `PGPASSWORD`. This
#                                over-redacts words that merely end in a suffix
#                                (`hockey:`); that is the documented fail-safe
#                                direction for a public body.)
#   6. quoted single token       the historical `(got 'AbCdEfGh')` leak vector
#   7. quoted long token         ≥20 b64/hex-ish, so `"TimeoutError"`,
#                                `"g_deadbeef01"` and `"graph_error_streaks"`
#                                stay readable
#   8. bare token-like run ≥20   a 22-char token escaped the earlier ≥24 floor
#   9. filesystem path
#
# Known residual (bounded, documented in docs/ops/registry-backup-dr.md): a
# secret with NO recognisable prefix that is split by raw whitespace into
# fragments each <20 chars. The primary control is at the SOURCE — the three
# key-parsing sites emit a sha256 fingerprint, never the raw value.
redact() { # text -> text safe for a public issue body / public Actions log
  printf '%s' "$1" | tr '\n\r\t' '   ' | sed -E \
    -e 's/\\"/"/g' \
    -e 's#([A-Za-z][A-Za-z0-9+.-]*://)[^[:space:]]*@#\1<redacted>@#g' \
    -e 's#([A-Za-z0-9_.-]+:)[^[:space:]]*@#\1<redacted>@#g' \
    -e 's/(Basic|Bearer|token|ApiKey|OAuth)[[:space:]]+[A-Za-z0-9+/=_.-]+/\1 <redacted>/Ig' \
    -e 's/(^|[^A-Za-z0-9_])(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|glpat-|xox[baprs]-|AKIA|ASIA|sk-)[^[:space:]]*/\1\2<redacted>/g' \
    -e "s/([A-Za-z0-9_.-]*(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PAT|AUTH|CREDENTIAL|APIKEY|DSN)[[:space:]]*[=:][[:space:]]*)(\"[^\"]*\"|'[^']*'|\"[^\"]*|'[^']*|[^[:space:]\"']+)/\\1<redacted>/Ig" \
    -e 's/("[A-Za-z0-9_.-]*(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PAT|AUTH|CREDENTIAL|APIKEY|DSN)"[[:space:]]*:[[:space:]]*)"[^"]*"/\1"<redacted>"/Ig' \
    -e "s/'([^']{6,})'/'<redacted>'/g" \
    -e 's/"([A-Za-z0-9+/=_.-]{20,})"/"<redacted>"/g' \
    -e 's/[A-Za-z0-9+/_.=-]{20,}/<redacted>/g' \
    -e 's#(/[A-Za-z0-9._-]+){3,}#<path>#g' || true
}
redact_truncate() { # text max_chars — redact BEFORE truncating (a secret must
  # not be cut into a sub-threshold fragment that escapes the run rules).
  redact "$1" | head -c "$2"
}

if [ -z "$API" ] || [ -z "$KEY" ]; then
  fail "INTERNAL_API_URL / FASTAPI_INTERNAL_KEY not set"
  exit 1
fi
if [ -z "$GH_TOKEN" ]; then
  # Review (Config/R2b): GitHub Actions does NOT export GITHUB_TOKEN into step
  # envs — the cron workflow must pass it explicitly. Without it every incident
  # POST/close is a 401, so the "loud" channel is dead while the job can still
  # look green. Fail closed.
  fail "GITHUB_TOKEN not set — DR incidents cannot be filed or closed; refusing to run blind"
  exit 1
fi

# ── dedup helpers (R2 create-once + GH-search fallback) ─────────────────────
GH_SEARCH_FAILED_SENTINEL="__gh_search_failed__"
r2_head() { # key -> 0 when the object EXISTS
  aws s3api head-object --endpoint-url "$R2_ENDPOINT" \
    --bucket "$R2_BUCKET" --key "$1" >/dev/null 2>&1
}
# Alert dedup keys (#2844). A subject-less (platform-scoped) incident is written
# by TWO implementations: this driver and the server-side AlertStore
# (tortoise/alert_store.py). They must agree on ONE key, or the R2 create-once is
# not a single linearization point: each writer wins its own object and files its
# own issue, and a resolve that deletes only one spelling strands the other
# carrying a CLOSED issue's number — whose 412 branch then re-files an incident
# that was just resolved.
#   canonical: ops/alerts/{KIND}/{subject}.json, `_` when there is no subject
#              (the spelling tortoise/alert_store.py writes)
#   legacy:    ops/alerts/{KIND}/global.json — this driver's pre-#2844 spelling
# Read and delete paths consult ALL spellings, so objects already in R2 are
# adopted and cleaned up rather than stranded. Subject-scoped incidents have
# exactly one key and are never crossed with another subject (#2375).
kind_owner() { # kind -> the writer whose probes cover this kind's recovery (#3127)
  # Mirrors AlertStore.KIND_OWNERS (tortoise/alert_store.py). The two MUST
  # agree — test_kind_owner_contract_with_driver pins them. Resolution authority
  # is evidence-gated: a writer whose probes do NOT cover the failing dependency
  # must never clear the incident, or a real fault is marked recovered and the
  # owner re-files it every run.
  case "$1" in
    # driver: its own R2 preflight + /status.storage_error + a measured pool.
    R2_DOWN|APP_DOWN|WATCHER_DOWN|SWEEP_CONFIG_ERROR|SWEEP_OFF_STALE|SWEEP_NO_COVERAGE|LIVENESS_NO_WORK)
      echo driver ;;
    # watcher: archive/stamp freshness + the driver heartbeat, read in-process.
    STALE|NEVER_BACKED_UP|METADATA_LOST|BACKUP_SET_MISSING|DRIVER_DOWN)
      echo watcher ;;
    # app: the drill resolves on its own success signal.
    RESTORE_DRILL_FAILED)
      echo app ;;
    *) echo unspecified ;;
  esac
}
alert_key() { # kind id -> the canonical dedup key for this incident
  local kind="$1" id="${2:-}"
  # #2844 (round-7 P2): canonicalization is for the EMPTY — subject-less — id
  # ONLY. `global` is this driver's pre-#2844 spelling of that same
  # subject-less incident, so it survives below as a legacy READ/DELETE alias
  # (see alert_keys_all), but it is NOT canonicalized here: a real subject
  # literally named `global` would then write `_.json` while the AlertStore's
  # `_keys()` keeps `global.json` for it — two create-once points for ONE
  # condition, the exact defect this PR exists to fix. Every PLATFORM call site
  # therefore passes `""`, never the literal "global".
  if [ -z "$id" ]; then id="_"; fi
  printf 'ops/alerts/%s/%s.json\n' "$kind" "$id"
}
alert_keys_all() { # kind id -> the canonical key + every legacy spelling
  local kind="$1" id="${2:-}"
  alert_key "$kind" "$id"
  # #2844: `global.json` is the pre-#2844 spelling of a SUBJECT-LESS incident,
  # so it is a legacy alias of the EMPTY id only. A real subject literally named
  # `global` (or `_`) owns its own single key outright — it is never an alias
  # set, and must not drag `global.json` in as a sibling spelling.
  if [ -z "$id" ]; then
    printf 'ops/alerts/%s/global.json\n' "$kind"
  fi
}
r2_put_once() { # key body_file -> 0 created, 1 already exists, 2 UNRESOLVED (loud)
  # #3032: mirror the Python twin (hosted_backup.create_if_not_exists) — a
  # rejected conditional write must never silently degrade the dedup AUTHORITY
  # into the fail-open GitHub title search. The pre-#3032 shape collapsed every
  # failure (412 race, unsupported `--if-none-match`, transport error, proxy)
  # into "the object already exists", so on a runner whose client rejects the
  # flag NO dedup object was ever created and dedup rested entirely on the
  # unverified search (the #2828 class).
  local out=""
  if out="$(aws s3api put-object --endpoint-url "$R2_ENDPOINT" \
    --bucket "$R2_BUCKET" --key "$1" --body "$2" --if-none-match "*" 2>&1)"; then
    return 0
  fi
  case "$out" in
    # The expected create-once race: the object already exists (S3 412).
    # Match the ERROR MARKERS only — a bare `*412*` would also match a
    # request-id / byte-count / timestamp in an unrelated failure and report
    # "exists" without ever HEAD-checking (review).
    *PreconditionFailed*|*"At least one of the pre-conditions"*) return 1 ;;
  esac
  # Conditional writes unsupported (or another error): fall back to the
  # HEAD-check — the object's EXISTENCE decides, exactly like the Python twin
  # (hosted_backup.create_if_not_exists). An AMBIGUOUS HEAD (absent read or a
  # read that failed) must NOT be followed by a blind unconditional put: it
  # could overwrite a concurrent writer's object and reset its issue_number to
  # null — the duplicate-risk class #3029 removes. Report unresolved instead.
  if r2_head "$1"; then return 1; fi
  # Include the (truncated) AWS cause: "conditional write rejected" alone cannot
  # distinguish an aws CLI that lacks --if-none-match (where this runner files
  # NOTHING and is red every hour) from broken creds or a transient network
  # fault — each needs a different operator action (final-cycle review P2).
  cause="$(printf '%s' "${out:-}" | tr '\n' ' ' | cut -c1-200)"
  fail "r2_put_once: could not create nor confirm $1 — conditional write rejected (${cause:-no output}) and the HEAD-check could not confirm absence. Dedup is unverified; refusing a blind put."
  return 2
}
r2_get() { # key -> body (empty on failure)
  aws s3api get-object --endpoint-url "$R2_ENDPOINT" --bucket "$R2_BUCKET" --key "$1" /dev/stdout 2>/dev/null || true
}
r2_delete() { # key — delete-to-resolve (the alert_store lifecycle contract)
  aws s3api delete-object --endpoint-url "$R2_ENDPOINT" --bucket "$R2_BUCKET" --key "$1" >/dev/null 2>&1 || true
}
# ── the ONE GitHub HTTP primitive ───────────────────────────────────────────
# EVERY GitHub call in this driver goes through `_gh_curl`/`gh_request`. A bare
# `curl -sS` exits 0 on an HTTP 4xx/5xx — the #2140 deaf-monitor class: a 403
# was reported as a successful write, and a failed SEARCH read as "no open
# issue" → a duplicate (#2706). The status is therefore captured and REQUIRED;
# a transport failure collapses to `000` and is refused the same way.
#
# This driver keeps its OWN single primitive (curl, not `gh api`) rather than a
# fourth one-off per call site. #5019 owns re-pointing the remaining inline
# filers — this one included, whose dedupe is R2-object-aware — at the shared
# `auto-file-issue.sh` substrate; until then status-checking exists ONCE here.
GH_API="https://api.github.com"

_gh_curl() { # <out-file> <method> <path> [curl args…] -> HTTP code on stdout
  local out="$1" method="$2" path="$3"; shift 3
  curl -sS -o "$out" -w '%{http_code}' -X "$method" \
    -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "$@" "${GH_API}${path}" 2>/dev/null || echo 000
}

gh_request() { # <method> <path> [curl args…] -> body on stdout; 0 ONLY on 2xx
  local method="$1" path="$2"; shift 2
  local tmp code
  tmp="$(mktemp)"
  code="$(_gh_curl "$tmp" "$method" "$path" "$@")"
  case "$code" in
    2[0-9][0-9]) cat "$tmp"; rm -f "$tmp"; return 0 ;;
  esac
  rm -f "$tmp"
  return 1
}

# EVERY page of a search query, concatenated into ONE JSON array. A non-2xx on
# ANY page is a FAILURE (non-zero): a partial pool must never be read as "the
# issue is not there", which files a duplicate. GitHub caps search at 1000
# results (10 pages of 100), which bounds the walk. Search ranking is
# relevance-based, NOT equality-first, so a single-page read can miss the exact
# issue and duplicate it (the class already fixed in the substrate's
# af_open_issue).
gh_search_items() { # <url-encoded-query> -> JSON array of items; non-zero on failure
  local q="$1" page=1 items="[]" json n tmp code
  tmp="$(mktemp)"
  while [ "$page" -le 10 ]; do
    code="$(_gh_curl "$tmp" GET "/search/issues?q=${q}&per_page=100&page=${page}")"
    case "$code" in
      2[0-9][0-9]) ;;
      *) rm -f "$tmp"; return 1 ;;
    esac
    json="$(cat "$tmp" 2>/dev/null || true)"
    n="$(printf '%s' "$json" | jq -r 'if (.items|type) == "array" then (.items|length) else "ERR" end' 2>/dev/null || echo ERR)"
    case "$n" in ''|*[!0-9]*) rm -f "$tmp"; return 1 ;; esac
    items="$(printf '%s\n%s' "$items" "$(printf '%s' "$json" | jq -c '.items' 2>/dev/null || echo '[]')" | jq -cs 'add')"
    [ "$n" -eq 100 ] || break
    page=$((page + 1))
  done
  rm -f "$tmp"
  printf '%s' "$items"
}

gh_find_open() { # kind id(subject) -> open issue number, "" when none, $GH_SEARCH_FAILED_SENTINEL when the search FAILED
  # #2375: subject-scoped — a bare kind search lets a per-graph issue
  # ("[DR] STALE — team_a:g_x") be adopted by a team-level file ("… team_a")
  # and vice versa (the bare team subject is a PREFIX of the per-graph
  # subject); recovery then closes the WRONG issue and orphans its dedup
  # object (silent-loss cross-talk — the server side is subject-scoped since
  # #2313; the driver must match). SUBJECT-LESS incidents (id="") keep the
  # kind-only match (their titles carry prose, not the id); every platform call
  # site passes `""` since #2844. `global` is the pre-#2844 spelling of that
  # subject-less incident but is NOT aliased here — a real subject literally
  # named `global` must match on its own TITLE, or it could adopt (and later
  # close) an unrelated platform incident.
  #
  # #3029: the search index is a RECALL filter, never an identity proof.
  # GitHub tokenizes punctuation away, so `in:title "[DR] R2_DOWN"` also matches
  # an ordinary bug report whose title merely contains the tokens — verified
  # live: the production query resolved to #2844, a bug report ABOUT R2_DOWN,
  # which the resolver would then have adopted and closed. Every hit is verified
  # against the incident's OWN title shape (the same contract the server
  # enforces in tortoise/github_issue.py::incident_title_matches): the kind must
  # follow `[DR] ` immediately, and a subject must be the EXACT ` — ` segment
  # after it. Subject-less ids ("") accept the bare `[DR] KIND` title plus any
  # trailing ` — prose`.
  #
  # The search is FAIL-CLOSED: a transport failure or an error body (a 403
  # rate-limit response is valid JSON with no `items`) prints the
  # GH_SEARCH_FAILED_SENTINEL instead of nothing, so no caller can read it as
  # "no incident" — the pre-#3029 `|| true` collapsed both into empty, which
  # made `file_alert` file a duplicate and let `resolve_global` close an
  # unverified target. The sentinel rides STDOUT (not a global) because callers
  # read this function through a command substitution, i.e. in a subshell.
  [ -n "$GH_TOKEN" ] || return 0
  local kind="$1" id="${2:-}" q items
  # One query shape for BOTH branches; the verification is applied to the
  # TITLE after the walk — the walk PAGES (a single page can miss the exact
  # subject, the #2706 class) and REFUSES on a failed search ("" would read as
  # "no incident" → duplicate).
  q="repo:${REPO}+is:issue+is:open+label:%22dr:backup%22+in:title+%22%5BDR%5D+$kind%22"
  if ! items="$(gh_search_items "$q")"; then
    printf '%s' "$GH_SEARCH_FAILED_SENTINEL"
    return 0
  fi
  printf '%s' "$items" | jq -r --arg k "$kind" --arg id "$id" '
    ("[DR] " + $k) as $p
    | [ .[]
        | (.title // "") as $t
        | select($t | startswith($p))
        | ($t[($p | length):]) as $rest
        | select(
            if ($id == "" or $id == "global") then
              ($rest == "" or ($rest | startswith(" — ")))
            else
              ($rest | startswith(" — "))
              and (($rest | ltrimstr(" — ") | split(" — ")[0]) == $id)
            end
          )
        | .number
      ][0] // empty' 2>/dev/null || { printf '%s' "$GH_SEARCH_FAILED_SENTINEL"; return 0; }
}
gh_issue_open() { # number -> 0 when OPEN or unknown; 1 when confirmed closed OR missing
  # #2796 (review R3/R4): the 412 dedup branch must trust the object over a GH
  # search, and must be able to tell a DELETED issue (404) from a transient
  # blip. A 404 is definitively gone → re-file (otherwise the recurrence is
  # swallowed forever). Rate-limit/5xx/network → assume OPEN, never duplicate.
  # ONE status-checked call via the primitive (the body and the code arrive
  # together, so the state read can never disagree with the code that admitted
  # it — the pre-fix code made TWO bare calls, the second of which could
  # answer a different body than the first).
  local n="${1:-}" tmp code state
  [ -n "$GH_TOKEN" ] && [ -n "$n" ] || return 0
  tmp="$(mktemp)"
  code="$(_gh_curl "$tmp" GET "/repos/${REPO}/issues/${n}")"
  case "$code" in
    404) rm -f "$tmp"; return 1 ;;   # definitively missing → re-file
    2[0-9][0-9]) : ;;
    *)   rm -f "$tmp"; return 0 ;;   # transport / rate-limit / 5xx → assume open
  esac
  state="$(jq -r '.state // empty' "$tmp" 2>/dev/null || true)"
  rm -f "$tmp"
  case "$state" in
    open) return 0 ;;
    "")   return 0 ;;   # unparseable body on a 200 → assume open
    *)    return 1 ;;   # closed
  esac
}
gh_comment() { # number body -> 0 ONLY when GitHub answered 2xx
  # #3907 review (P1): a bare `curl -sS …` exits 0 on an HTTP 4xx/5xx — the
  # comments endpoint answered 403 (secondary rate limit / missing `issues:
  # write` / abuse detection) with exit 0 in review, and with core quota at
  # 4,998. Called as `if gh_comment …`, that made gh_record_occurrence log
  # "recorded recurrence #N … (no duplicate filed)" while the issue carried NO
  # record at all — the #2140 deaf-monitor class this file already calls out.
  # So capture the status code and require a REAL 2xx via the ONE primitive
  # above; a transport failure (curl exits non-zero, no code) collapses to
  # `000` and is refused the same way.
  [ -n "$GH_TOKEN" ] || return 0
  gh_request POST "/repos/${REPO}/issues/$1/comments" \
    -d "$(jq -nc --arg b "$2" '{body:$b}')" >/dev/null
}
# #3907: a dedup no-op must still RECORD the occurrence. The pre-#3907 dedup
# paths logged and returned, so a driver firing hourly on ONE unchanged fault
# looked like a single quiet run in the issue — dedupe that hides the
# escalation converts "noisy" into "blind", and #3907's acceptance requires the
# recurrence to be visible on the issue. The count is read from the ISSUE (our
# own marked comments), never from the R2 dedup object: the object is shared
# with the server-side AlertStore, which rewrites it, so a counter kept there
# would be reset by the other writer.
OCCURRENCE_MARKER='<!-- dr-occurrence -->'
# The only author whose marked comment may be counted. The marker is not secret
# (the public comments API returns it verbatim), so a bare `contains($m)` let ANY
# third party post the marker and inflate the recurrence number — the one field
# of #3907 an outsider can corrupt. Filtering on the reserved bot login closes
# the over-count direction; the count may still under-report on a read failure,
# which only re-states a lower N (harmless).
OCCURRENCE_BOT_LOGIN='github-actions[bot]'
OCCURRENCE_MAX_PAGES=20
gh_occurrence_count() { # number -> integer (0 when unreadable: under-count, never fabricate)
  local n="${1:-}" page=1 total=0 body cnt len
  [ -n "$GH_TOKEN" ] && [ -n "$n" ] || { printf '0'; return 0; }
  while [ "$page" -le "$OCCURRENCE_MAX_PAGES" ]; do
    if ! body="$(gh_request GET "/repos/${REPO}/issues/${n}/comments?per_page=100&page=${page}")"; then
      printf '%s' "$total"; return 0
    fi
    cnt="$(printf '%s' "$body" | jq -r --arg m "$OCCURRENCE_MARKER" --arg login "$OCCURRENCE_BOT_LOGIN" \
      '[.[]? | select(((.user.login // "") == $login) and ((.body // "") | contains($m)))] | length' 2>/dev/null || true)"
    len="$(printf '%s' "$body" | jq -r 'if type == "array" then length else -1 end' 2>/dev/null || echo -1)"
    case "$cnt" in ''|*[!0-9]*) cnt=0 ;; esac
    case "$len" in ''|*[!0-9]*) len=-1 ;; esac
    total=$((total + cnt))
    [ "$len" -eq 100 ] || break
    page=$((page + 1))
  done
  printf '%s' "$total"
}
gh_record_occurrence() { # number kind id
  # Additive only: it comments and logs, and NEVER changes LOUD or the incident
  # lifecycle. A failing comment is logged, not fatal — the run is already RED
  # (file_alert set LOUD before any dedup branch), so the escalation the comment
  # records is never the sole carrier of the signal.
  local n="${1:-}" kind="${2:-}" id="${3:-}" count next
  [ -n "$n" ] || return 0
  count="$(gh_occurrence_count "$n")"
  case "$count" in ''|*[!0-9]*) count=0 ;; esac
  next=$((count + 1))
  if gh_comment "$n" "$(printf '🔁 **Recurrence #%s** — `[DR] %s — %s` observed again at `%s`. This incident is already tracked by this issue, so no duplicate was filed.\n\n%s' \
      "$next" "$kind" "${id:-_}" "$(date -u +%FT%TZ)" "$OCCURRENCE_MARKER")"; then
    log "dedup: recorded recurrence #${next} on issue #${n} (no duplicate filed)"
  else
    # Never fatal: LOUD is already set, so the run is RED without the comment —
    # the next run re-records (the count is derived from the issue, so nothing
    # is skipped). Say so rather than claiming a record that did not happen.
    log "dedup: recurrence comment on issue #${n} FAILED — the run is still RED; the next run re-records"
  fi
}
gh_close() { # number comment kind id -> 0 ONLY when the close was CONFIRMED 2xx
  [ -n "$GH_TOKEN" ] || return 0
  local kind="${3:-}" id="${4:-}" code=""
  # #3907 review (P2) / #3029-#3031 class, cycle-2 review P1 — the shell twin of
  # the Python fix in alert_store.resolve_incident. The pre-fix close was
  # `curl … >/dev/null 2>&1 || true`, so a 403/5xx (or a transport failure) left
  # the issue OPEN while the driver believed it resolved — AND deleted the R2
  # sentinel, so the next recurrence re-adopted a stale issue and a human saw
  # "unresolved" indefinitely. The close must SUCCEED before we drop the dedup
  # object: deleting on a failed PATCH leaves the object gone while the issue
  # stays OPEN — the next run re-creates the object, adopts the still-open issue
  # and pages again, and `resolve_global` has already set the run green, so the
  # false all-clear is invisible. On a non-2xx the object is KEPT and the failure
  # is LOUD so the next hourly run retries.
  curl -sS -X POST -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPO}/issues/$1/comments" \
    -d "$(jq -nc --arg b "$2" '{body:$b}')" >/dev/null 2>&1 || true
  code="$(curl -sS -o /dev/null -w '%{http_code}' -X PATCH \
    -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPO}/issues/$1" -d '{"state":"closed"}' 2>/dev/null)" || code=""
  case "$code" in
    2*) : ;;
    *)
      LOUD=1
      fail "gh_close: closing issue #$1 returned HTTP ${code:-<no response>} — keeping the dedup object so the next run retries (the issue is still OPEN)"
      return 1 ;;
  esac
  # delete-to-resolve: drop the R2 dedup object so a RECURRENCE is a new
  # incident. Without this, file_alert's 412 branch would adopt the stale
  # object and silently swallow the recurrence (the #2796 class).
  if [ -n "$kind" ]; then
    # #2844: delete EVERY spelling of this incident's sentinel. Deleting one and
    # leaving the other strands it holding a closed issue's number, and the next
    # detection re-files the incident that was just resolved.
    local _k
    while IFS= read -r _k; do r2_delete "$_k"; done < <(alert_keys_all "$kind" "$id")
  fi
  return 0
}
resolve_global() { # kind comment — close an open global incident (no-op if none)
  local kind="$1" comment="$2" num="" owner=""
  # #3127: refuse to clear a kind this driver has no evidence for. Without this
  # the driver's generic sweep-completed self-heal would close incidents the
  # driver never observed recovering.
  owner="$(kind_owner "$kind")"
  # #3127: authority is decided by the owner map ALONE. There is deliberately no
  # "but I opened it myself" exception — three review rounds found three ways a
  # self-asserted filed-by note went wrong, each letting a non-owner clear a
  # kind its probes never covered. A future call site for a watcher-owned kind
  # must therefore NOT be routed through resolve_global.
  if [ "$owner" != "driver" ] && [ "$owner" != "unspecified" ]; then
    log "self-heal: refusing to close ${kind} — it is owned by the ${owner}, whose probes cover its recovery condition"
    return 0
  fi
  # #2844: the platform incident is written under the canonical `_` spelling;
  # every platform call site passes `""` (never the literal "global", which is a
  # real subject's own key and a legacy READ alias).
  num="$(gh_find_open "$kind" "")"
  case "$num" in
    "$GH_SEARCH_FAILED_SENTINEL")
      # #3029 fail-closed: a failed search cannot tell "no incident" from
      # "cannot see incidents", so the target is unverifiable and closing is a
      # guess — the pre-#3029 fuzzy search closed whatever it returned (the
      # #2844 class). Close nothing; the next run retries. LOUD, never silent.
      fail "GitHub search failed for ${kind} — skipping resolve (target unverifiable, not guessing); the next run retries"
      LOUD=1
      return 0 ;;
    ''|*[!0-9]*) return 0 ;;
  esac
  if ! gh_close "$num" "$comment" "$kind" ""; then
    log "self-heal: could NOT close issue #${num} for ${kind} — it stays OPEN with its dedup object"
  fi
}
telegram() { # text
  [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] \
    && curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" --data-urlencode "text=$1" >/dev/null 2>&1 || true
}
# Create ONE issue, status-checked, and return its number ("" when UNFILED).
# This is the SINGLE create call both file_alert branches used to duplicate
# (#3907 review P2): the pre-fix bare `curl … | jq -r '.number // empty'`
# conflated an HTTP failure with "no number", so a 403/5xx filed nothing while
# `finish()` claimed "an incident was filed".
gh_create_issue() { # <title> <body> -> issue number on stdout, "" when UNFILED
  local title="$1" body="$2" resp n
  if ! resp="$(gh_request POST "/repos/${REPO}/issues" \
      -d "$(jq -nc --arg t "$title" --arg b "$body" '{title:$t, body:$b, labels:["dr:backup"]}')")"; then
    fail "GitHub issue CREATE failed (HTTP error) — the '${title}' finding is UNFILED; the dedup object is kept so the next run re-files it"
    printf ''
    return 0
  fi
  n="$(printf '%s' "$resp" | jq -r '.number // empty' 2>/dev/null || true)"
  if [ -z "$n" ]; then
    fail "GitHub issue CREATE answered 2xx without an issue number — treating the finding as UNFILED"
  fi
  printf '%s' "$n"
}
file_alert() { # kind title body dedup_id
  local kind="$1" title="$2" body="$3" id="$4" num="" tmp="" issue_num="" key="" filed=0 rc=0
  LOUD=1
  tmp="$(mktemp)"
  key="$(alert_key "$kind" "$id")"
  # #2844: another writer (the server-side AlertStore, or this driver pre-#2844)
  # may already hold this incident under a DIFFERENT spelling. Consult every
  # spelling BEFORE creating ours, or one condition gets two create-once points
  # and each writer files its own issue.
  local _k alias_num=""
  while IFS= read -r _k; do
    if [ "$_k" = "$key" ]; then continue; fi
    alias_num="$(printf '%s' "$(r2_get "$_k")" | jq -r '.issue_number // empty' 2>/dev/null || true)"
    if [ -n "$alias_num" ] && [ -z "${alias_num//[0-9]/}" ] && gh_issue_open "$alias_num"; then
      log "dedup: ${kind} already tracked by open issue #${alias_num} (alias ${_k}) — no-op"
      gh_record_occurrence "$alias_num" "$kind" "$id"
      rm -f "$tmp"
      return 0
    fi
  done < <(alert_keys_all "$kind" "$id")
  printf '{"kind":"%s","issue_number":null,"filed_at":"%s"}' "$kind" "$(date -u +%FT%TZ)" > "$tmp"
  # #3032: three outcomes, not two — 0 created, 1 already exists (the 412
  # race), 2 dedup UNRESOLVED (unsupported conditional write + no provable
  # object). Only 0/1 may proceed; 2 must never reach the search-only path.
  if r2_put_once "$key" "$tmp"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" = "2" ]; then
    fail "dedup unresolved for ${kind}/${id:-_} — refusing to continue on search-only dedup"
    LOUD=1
    rm -f "$tmp"
    return 0
  fi
  if [ "$rc" = "0" ]; then
    num="$(gh_find_open "$kind" "$id")"
    if [ "$num" = "$GH_SEARCH_FAILED_SENTINEL" ]; then
      # #3029 fail-closed (#2706 direction): the create succeeded but the search
      # could not run, so whether an issue for this incident already exists is
      # UNKNOWN. A failed search must never read as "no incident" — filing now
      # could duplicate the open issue. The placeholder object stays with
      # issue_number:null, so the next run's 412 branch retries. LOUD, never
      # silent. The create-once object above is already written.
      num=""
      fail "dedup: the issue search FAILED for ${kind}/${id:-_} — refusing to file a possible duplicate; filing DEFERRED (a failed search is not 'no incident'); the dedup object remains for the next run"
      LOUD=1
    elif [ -z "$num" ]; then
      num="$(gh_create_issue "$title" "$body")"
      [ -n "$num" ] && filed=1
    fi
    if [ -n "$num" ]; then
      # Review F7 (coherence): backfill the AUTHORITATIVE R2 object with the
      # issue number here too. Without it the object keeps issue_number=null
      # until the next run, so a transient empty GitHub search in that window
      # would create a duplicate (the 412 object-trust path cannot help).
      # Provenance is claimed ONLY when this driver created the issue (#3127),
      # never when adopting one — stamping an ADOPTED issue as driver-filed is
      # the exact defect the store fixed in Python. The `writer` field is
      # DIAGNOSTIC ONLY: resolution authority is decided by KIND_OWNERS ALONE,
      # here (`resolve_global`) and in the store (`resolve_incident`), never by
      # this field — there is deliberately no provenance exception.
      _w=""; [ "$filed" = "1" ] && _w=',"writer":"driver"'
      printf '{"kind":"%s","issue_number":%s,"filed_at":"%s"%s}' "$kind" "$num" "$(date -u +%FT%TZ)" "$_w" > "$tmp"
      aws s3api put-object --endpoint-url "$R2_ENDPOINT" --bucket "$R2_BUCKET" \
        --key "$key" --body "$tmp" >/dev/null 2>&1 || true
      telegram "🚨 DR alert: ${kind} — issue #${num}"
    fi
  else
    # 412 — the object exists, so a prior creator won the create-once race.
    # Read it: the object is the AUTHORITATIVE dedup state, and a GH search
    # alone can lag or transiently return [] (which would duplicate the
    # issue). Two shapes reach here:
    #   (a) the winner created the object but died before backfilling an issue
    #       number (create-then-die — review P2-3);
    #   (b) a RESOLVED incident is recurring — the dedup object outlived the
    #       closed issue, and its stale issue_number would otherwise swallow
    #       the recurrence silently (the #2796 class: a condition that pages
    #       once must page again after it recurs).
    # If the recorded issue is still OPEN this incident is already tracked —
    # stop. Otherwise adopt an OPEN issue for this (kind, subject) if one
    # exists, else become the filer, then backfill our issue_number.
    issue_num="$(printf '%s' "$(r2_get "$key")" | jq -r '.issue_number // empty' 2>/dev/null || true)"
    if [ -n "$issue_num" ] && [ -z "${issue_num//[0-9]/}" ] && gh_issue_open "$issue_num"; then
      log "dedup: ${kind}/${id:-_} already tracked by open issue #${issue_num} — no-op"
      gh_record_occurrence "$issue_num" "$kind" "$id"
      rm -f "$tmp"
      return 0
    fi
    num="$(gh_find_open "$kind" "$id")"
    if [ "$num" = "$GH_SEARCH_FAILED_SENTINEL" ]; then
      # Same refusal as the create-once branch above: a failed search must never
      # become a duplicate issue; the dedup object is kept for the next run.
      num=""
      fail "dedup: the issue search FAILED for ${kind}/${id:-_} — refusing to file a possible duplicate; filing DEFERRED (412 branch); the dedup object is kept for the next run"
      LOUD=1
    elif [ -z "$num" ]; then
      num="$(gh_create_issue "$title" "$body")"
      [ -n "$num" ] && filed=1
    fi
    if [ -n "$num" ]; then
      _w=""; [ "$filed" = "1" ] && _w=',"writer":"driver"'
      printf '{"kind":"%s","issue_number":%s,"filed_at":"%s"%s}' "$kind" "$num" "$(date -u +%FT%TZ)" "$_w" > "$tmp"
      aws s3api put-object --endpoint-url "$R2_ENDPOINT" --bucket "$R2_BUCKET" \
        --key "$key" --body "$tmp" >/dev/null 2>&1 || true
      telegram "🚨 DR alert: ${kind} — issue #${num}"
    fi
  fi
  rm -f "$tmp"
}

# ── 0. aws preflight + DIRECT R2 freshness (the app-down/crash-loop leg) ─────
# A failed listing is NEVER confirmed-empty (no STALE/NEVER from a failed
# read); a broken R2 auth preflight files R2_DOWN loudly (review P3).
R2_OK=1
# #2796: pool-level signals retained from the app-independent leg and consumed
# by the kill-switch classifier (off-while-stale) and the no-coverage check
# (enabled but 0 teams backed up) below.
POOL_STALE=0        # a team's newest default archive is older than DRIVER_DOWN_MIN
R2_TEAM_COUNT=0     # team prefixes present under backups/
# #2796 (review P1): `aws --output text` renders a LIST as ONE tab-separated
# line, so a bare `while read` measured only the LAST team. Split on tabs first
# (process substitution keeps the current shell, so the counters propagate).
# #2796 (review R5): a FAILED listing is UNKNOWN, not empty. Without this flag
# an R2 read that lacks ListObjects (or a transient failure) would look like
# "pool empty" — suppressing SWEEP_NO_COVERAGE and reporting a stale pool as
# fresh. R2_LIST_OK stays 1 only when the top-level AND every per-team listing
# succeeded (the whole pool is genuinely measured).
R2_LIST_OK=0
# Bucket-scoped probe (head-bucket) — R2's S3 API does not reliably support
# the account-level ListBuckets call from an object-scoped access key.
if ! aws s3api head-bucket --endpoint-url "$R2_ENDPOINT" --bucket "$R2_BUCKET" >/dev/null 2>&1; then
  R2_OK=0
  log "R2 preflight failed — filing R2_DOWN"
  file_alert R2_DOWN "[DR] R2_DOWN — backup storage unreachable" \
    "R2 preflight (head-bucket) failed from the driver. Runbook: docs/ops/registry-backup-dr.md" ""
fi

if [ "$R2_OK" = "1" ]; then
  # #3659: decode the top-level listing as JSON, not `--output text`. botocore
  # renders a null JMESPath result as the literal "None" under text — which is
  # indistinguishable from a genuine team id, so an object-EMPTY pool read as a
  # team named "None" (and a real "None" team would be dropped). jq maps an
  # absent CommonPrefixes (`null`) to the measured-EMPTY pool it truly is.
  # The decode is GATED: an unparseable body is UNKNOWN, never an empty pool.
  TOP_ERR="$(mktemp)"
  if TEAMS_JSON="$(aws s3api list-objects-v2 --endpoint-url "$R2_ENDPOINT" \
    --bucket "$R2_BUCKET" --prefix "backups/" --delimiter "/" --query "CommonPrefixes[].Prefix" \
    --output json 2>"$TOP_ERR")"; then
    if TEAMS="$(printf '%s' "$TEAMS_JSON" | jq -r 'if . == null then empty else .[] end' 2>/dev/null)"; then
      R2_LIST_OK=1
    else
      # A body the decoder could not read is not a measured-empty pool.
      R2_LIST_OK=0
      TEAMS=""
      log "R2 top-level listing UNPARSEABLE — pool state is UNKNOWN (not empty)"
    fi
  else
    TEAMS=""
    top_err_txt="$(redact_truncate "$(cat "$TOP_ERR")" 300)"
    log "R2 top-level listing failed — pool state is UNKNOWN (not empty) (${top_err_txt:-no stderr})"
  fi
  rm -f "$TOP_ERR"
  if [ -n "$TEAMS" ]; then
    # Review F5 (security): a predictable /tmp path is a symlink/overwrite
    # hazard on a shared runner and can be read back stale. Use mktemp.
    IDX_ERR="$(mktemp)"
    # #3659: the per-team listing stderr is captured (not /dev/null'd) so a
    # genuine failure is DIAGNOSABLE — mirroring IDX_ERR. Without it the run
    # log carried only the driver's own sentence and could not be told apart
    # from the empty-prefix edge case it was actually hitting.
    DEFAULT_ERR="$(mktemp)"
    FLAT_ERR="$(mktemp)"
    # `while read` rather than `for $TEAMS`: an unquoted expansion word-splits
    # AND glob-expands, so a bucket key containing `*` or whitespace would
    # fabricate team names carried into R2 keys and incident titles (security
    # review). Skip keys that are not our validated team-id shape.
    while IFS= read -r prefix; do
      [ -n "$prefix" ] || continue
      org_id="$(basename "$prefix")"
      case "$org_id" in
        ''|*[!A-Za-z0-9_-]*)
          log "skipping unexpected team prefix '${prefix}' (not a valid team id)"
          continue
          ;;
      esac
      R2_TEAM_COUNT=$((R2_TEAM_COUNT + 1))
      # #2375: DEFAULT-graph freshness ONLY — nested default segment
      # (backups/{org_id}/default/) + legacy flat (pre-#2313 default dumps;
      # flat keys start with the dump year "2xxx" so the 2-prefix never
      # matches custom nested gids g_*). The pre-#2375 leg took the newest
      # dump.enc under the WHOLE team prefix, so a team whose DEFAULT failed
      # for > STALE_MIN while a custom graph backed up fresh read "not
      # stale" — exactly the app-down case this leg exists to cover. A
      # legacy-flat classification index (#2370) additionally excludes
      # C5-era custom flat dumps when present (flat-only fallback is parity
      # pre-index).
      # #3659: the DEFAULT-archive listing must be TOTAL on an empty prefix.
      # S3 omits `Contents` entirely when nothing matches, and a `sort_by()`
      # aggregator over that absent element raises JMESPathTypeError, so the
      # aws CLI exits non-zero — an EMPTY prefix was read as a LISTING
      # FAILURE, set the GLOBAL R2_LIST_OK=0, and escalated to a platform-wide
      # R2_DOWN while the top-level listing had succeeded in the same run.
      # The plain projection is total (absent/empty -> `null`/`[]`) and jq
      # takes the max — the same `max(LastModified)` shape the legacy-flat leg
      # already uses. An empty prefix is a MEASURED result, not an outage.
      default_ok=1
      newest=""
      if ! default_raw="$(aws s3api list-objects-v2 --endpoint-url "$R2_ENDPOINT" \
        --bucket "$R2_BUCKET" --prefix "backups/${org_id}/default/" \
        --query "Contents[?ends_with(Key, 'dump.enc')].LastModified" \
        --output json 2>"$DEFAULT_ERR")"; then
        default_ok=0
        default_err_txt="$(redact_truncate "$(cat "$DEFAULT_ERR")" 300)"
        log "team ${org_id}: default-archive listing FAILED — freshness UNKNOWN (${default_err_txt:-no stderr})"
      else
        newest="$(printf '%s' "$default_raw" | jq -r 'if type == "array" and length > 0 then max else empty end' 2>/dev/null || true)"
      fi
      flat_ok=1
      if ! flat_list="$(aws s3api list-objects-v2 --endpoint-url "$R2_ENDPOINT" \
        --bucket "$R2_BUCKET" --prefix "backups/${org_id}/2" \
        --query "Contents[?ends_with(Key, 'dump.enc')].[Key,LastModified]" \
        --output json 2>"$FLAT_ERR")"; then
        flat_list="[]"
        flat_ok=0
        flat_err_txt="$(redact_truncate "$(cat "$FLAT_ERR")" 300)"
        log "team ${org_id}: legacy-flat listing FAILED — freshness UNKNOWN (${flat_err_txt:-no stderr})"
      elif [ -z "$flat_list" ] || [ "$flat_list" = "null" ]; then
        # #3659: an EMPTY flat prefix is MEASURED too. botocore renders an
        # absent `Contents` as the JSON literal `null` (not `[]`), which the
        # `[ "$flat_list" != "[]" ]` guard would treat as a non-empty pool and
        # route into flat classification — where an index-read error would
        # blank a MEASURED default archive and file a false R2_DOWN. Normalize
        # null/empty to the empty list.
        flat_list="[]"
      fi
      if [ "$default_ok" = "0" ] || [ "$flat_ok" = "0" ]; then
        # A failed per-team listing CALL is unmeasurable, never "no archive"
        # (review R2a) — but it must NOT swallow the leg that DID answer
        # (#3659 defect 2: the old code `continue`d here before consuming
        # flat_list, so a legacy-flat default archive was never measured).
        R2_LIST_OK=0
      fi
      if [ "$default_ok" = "0" ] && [ "$flat_ok" = "0" ]; then
        # BOTH legs unmeasured — nothing to evaluate for this org. The pool is
        # already flagged unmeasurable (R2_LIST_OK=0) above.
        continue
      fi
      if [ "$flat_ok" = "1" ] && [ -n "$flat_list" ] && [ "$flat_list" != "[]" ]; then
        # Review P2 (bug-deep): a FAILED read of the classification index is
        # NOT "no index" — a custom flat could then be misread as a default
        # dump and mask a stale default. Only a definitive absence
        # (NoSuchKey/404) means the pre-#2370 parity.
        idx=""; idx_rc=0
        idx="$(aws s3api get-object --endpoint-url "$R2_ENDPOINT" --bucket "$R2_BUCKET" \
          --key "ops/legacy-flat-index/${org_id}.json" /dev/stdout 2>"$IDX_ERR")" || idx_rc=$?
        if [ "$idx_rc" != "0" ] && ! grep -qiE 'NoSuchKey|404|Not Found' "$IDX_ERR" 2>/dev/null; then
          log "team ${org_id}: legacy-flat index read FAILED — freshness UNKNOWN"
          R2_LIST_OK=0
          continue
        fi
        if [ -n "$idx" ] && [ "$(printf '%s' "$idx" | jq -r 'type' 2>/dev/null)" = "object" ]; then
          flat_newest="$(printf '%s' "$flat_list" | jq -r --argjson idx "$idx" \
            '[.[] | select((($idx[(.[0] | split("/")[1] + "/" + split("/")[2])].graph_id) // "") == "" or ($idx[(.[0] | split("/")[1] + "/" + split("/")[2])].graph_id) == "default") | .[1]] | max // empty' 2>/dev/null || true)"
        else
          # no index (pre-#2370) — flat pool is default parity
          flat_newest="$(printf '%s' "$flat_list" | jq -r '[.[] | .[1]] | max // empty' 2>/dev/null || true)"
        fi
        if [ -n "$flat_newest" ]; then
          if [ -n "$newest" ]; then
            newest="$(printf '%s\n%s' "$newest" "$flat_newest" | sort | tail -1)"
          else
            newest="$flat_newest"
          fi
        fi
      fi
      if [ -n "$newest" ]; then
        newest_ts="$(date -d "$newest" +%s 2>/dev/null || echo 0)"
        if [ "$newest_ts" = "0" ]; then
          # An unparseable timestamp is an unmeasurable pool — fail loud, never
          # report it fresh.
          log "team ${org_id}: unparseable archive timestamp '${newest}' — treating pool as stale"
          POOL_STALE=1
        else
          age_min=$(( ($(date +%s) - newest_ts) / 60 ))
          if [ "$age_min" -gt "$STALE_MIN" ]; then
            log "team ${org_id}: newest archive ${age_min}m old — filing STALE (direct leg)"
            file_alert STALE "[DR] STALE — ${org_id}" "Direct R2 freshness check: newest archive ${age_min}m old (> ${STALE_MIN}m)." "$org_id"
          fi
          if [ "$age_min" -gt "$DRIVER_DOWN_MIN" ]; then
            POOL_STALE=1
          fi
        fi
      else
        if [ "$default_ok" = "1" ] && [ "$flat_ok" = "1" ]; then
          # Team prefix present but BOTH legs measured EMPTY: this graph has
          # NO restorable dump. Never-backed-up is worse than old, so a
          # disabled sweep must not read this as a fresh pool (#2796 review
          # R5). Only reachable when the listings ANSWERED (an empty prefix is
          # a measurement, not a failure — #3659).
          log "team ${org_id}: team prefix present but no default archive — treating pool as stale"
          POOL_STALE=1
        else
          # An absence we could not confirm is NOT an absence (unknown != no
          # archive): a failed listing call leaves this org unmeasured, and
          # the pool is already flagged unmeasurable above. Never fabricate a
          # "no default archive" from a read that did not answer (#3659
          # defect 3).
          log "team ${org_id}: no archive measured (a listing call failed) — pool UNKNOWN"
        fi
      fi
    done < <(printf '%s\n' "$TEAMS" | tr '\t' '\n')
    rm -f "$IDX_ERR" "$DEFAULT_ERR" "$FLAT_ERR"
  fi
fi

# A preflight that passes but a listing that fails is a PARTIAL storage outage:
# the pool cannot be measured, so freshness/coverage cannot be verified (review
# P3, coherence). This is the same "unknown ≠ empty" rule as everywhere else
# and it must not be green just because the sweep claims a backup. R2_DOWN is
# the right kind: its self-heal already requires a measured pool, so it will
# not be closed by the same blind run.
if [ "$R2_OK" = "1" ] && [ "$R2_LIST_OK" != "1" ]; then
  log "R2 preflight passed but the pool listing failed — storage is only partially reachable; filing R2_DOWN"
  file_alert R2_DOWN "[DR] R2_DOWN — backup storage not listable" \
    "head-bucket succeeded but one or more list-objects-v2 calls failed (R2_LIST_OK=0). The pool cannot be measured, so archive freshness and coverage cannot be verified. Check the R2 access key's ListObjects permission." ""
fi

# ── 1. pre-flight status ────────────────────────────────────────────────────
STATUS=""
if [ "$SIMULATE_APP_DOWN" = "true" ]; then
  log "simulate_app_down: pointing pre-flight at a dead URL"
  STATUS=""
else
  STATUS="$(curl -sS -m 20 -H "Authorization: Bearer $KEY" \
    "${API}/v1/internal/backups/status" 2>/dev/null || true)"
fi

if [ -z "$STATUS" ]; then
  # connect/DNS/timeout/5xx-without-app-body → APP_DOWN. The direct-R2 STALE
  # filings above already ran, so the app-down case still surfaces staleness.
  # #2796 (review P2-3): an app-down run is a BROKEN pipeline — file the
  # incident and go RED (finish reads the LOUD flag). The old exit 0 is what
  # let a 31-day outage hide behind 40 green runs.
  log "app unreachable — filing APP_DOWN"
  file_alert APP_DOWN "[DR] APP_DOWN — app unreachable" \
    "The hosted API did not answer /status. Runbook: docs/ops/registry-backup-dr.md" ""
  finish
fi

# #2796 (review R5): require a real boolean. A missing/mistyped .enabled
# (schema drift) or a non-JSON 200 body must fail CLOSED — the pre-fix
# `// false` default read both as a deliberate pause (silent).
ENABLED="$(printf '%s' "$STATUS" | jq -r 'if (.enabled|type)=="boolean" then (.enabled|tostring) else "unknown" end' 2>/dev/null || echo unknown)"
STORAGE_ERR="$(printf '%s' "$STATUS" | jq -r '.storage_error // empty' 2>/dev/null || true)"
CONFIG_ERR="$(printf '%s' "$STATUS" | jq -r '.config_error // empty' 2>/dev/null || true)"
# #2796 (review R2/R4): these strings get PUBLISHED to a public GitHub issue +
# Telegram, and the app's error text can embed key material (e.g.
# backup_config.py's "must be base64 (got '<key prefix>'...)"). Redact first.
CONFIG_ERR_SAFE="$(redact "$CONFIG_ERR")"
STORAGE_ERR_SAFE="$(redact "$STORAGE_ERR")"
LAST_SWEEP_AT="$(printf '%s' "$STATUS" | jq -r '.last_sweep.last_sweep_at // empty' 2>/dev/null || true)"
# Review R2a/R2b/Security: `last_sweep` carries `graph_failures[].error` (raw
# per-graph exception text) and is published in an incident body — redact it.
LAST_SWEEP_SAFE="$(redact "$(printf '%s' "$STATUS" | jq -c '.last_sweep' 2>/dev/null || echo null)")"
log "status: enabled=$ENABLED storage_error=${STORAGE_ERR_SAFE:-none} config_error=${CONFIG_ERR_SAFE:-none}"
# #5028: `per_team` (the whole org census) is serialized BEFORE `last_sweep`, so
# a 600-char truncation of the raw blob ALWAYS cuts the sweep OUTCOME off the
# end — the one field a diagnosis needs. Log the outcome untruncated on its own
# line so it can never be hidden behind the roster.
ORG_COUNT="$(printf '%s' "$STATUS" | jq -r 'if (.per_team|type)=="object" then (.per_team|length|tostring) else "unknown" end' 2>/dev/null || echo unknown)"
[ -n "$ORG_COUNT" ] || ORG_COUNT=unknown
log "sweep outcome: last_sweep=$LAST_SWEEP_SAFE orgs=$ORG_COUNT"
log "raw status: $(redact_truncate "$STATUS" 600)"

if [ "$ENABLED" = "unknown" ]; then
  # Fail CLOSED, never silent: an unclassifiable /status must not look like a
  # deliberate pause.
  log "unparseable /status (no boolean .enabled) — filing SWEEP_NO_COVERAGE (job red)"
  file_alert SWEEP_NO_COVERAGE "[DR] SWEEP_NO_COVERAGE — /status unclassifiable" \
    "GET /status returned 200 but carried no boolean .enabled field (schema drift, or a non-JSON body). The driver fails CLOSED rather than reading an unknown shape as a deliberate pause. Check the app version and the /status contract." ""
  fail "unparseable /status — no boolean .enabled field"
  exit 1
fi

if [ "$ENABLED" != "true" ]; then
  # #2796: enabled:false conflates four states. Only a genuine deliberate
  # pause (no config error, no storage error, fresh pool) may exit silently.
  if [ -n "$CONFIG_ERR" ]; then
    log "kill-switch is NOT an operator decision — config_error is set; filing SWEEP_CONFIG_ERROR (job red)"
    file_alert SWEEP_CONFIG_ERROR "[DR] SWEEP_CONFIG_ERROR — backups off, config broken" \
      "enabled=false with config_error: ${CONFIG_ERR_SAFE}. The sweep flag says 'run' but load_config() raised, so nothing can be written. Fix the Fly secret/config (runbook: docs/ops/registry-backup-dr.md §REGISTRY_STREAM_KEY), then re-run." ""
    fail "sweep disabled by a configuration error: ${CONFIG_ERR_SAFE}"
    exit 1
  fi
  # Config is readable again (config_error is null here) — clear the incident.
  resolve_global SWEEP_CONFIG_ERROR "Resolved — load_config() no longer raises."
  if [ -n "$STORAGE_ERR" ]; then
    log "status reports a storage error — filing R2_DOWN (not a kill-switch)"
    file_alert R2_DOWN "[DR] R2_DOWN — app storage unavailable" "status.storage_error: $STORAGE_ERR_SAFE" ""
    fail "backups disabled by a storage error: ${STORAGE_ERR_SAFE}"
    exit 1
  fi
  if [ "$POOL_STALE" = "1" ]; then
    log "kill-switch while the R2 pool is stale (oldest > ${DRIVER_DOWN_MIN}m) — filing SWEEP_OFF_STALE (job red)"
    file_alert SWEEP_OFF_STALE "[DR] SWEEP_OFF_STALE — backups off and the pool is stale" \
      "enabled=false with no config_error, but the direct-R2 leg found a default archive older than ${DRIVER_DOWN_MIN}m (or a team with none at all) among ${R2_TEAM_COUNT} team prefix(es). An intentional pause must not let the pool decay unnoticed: re-enable backups or declare a bounded pause." ""
    fail "backups disabled while the R2 pool is stale (> ${DRIVER_DOWN_MIN}m)"
    exit 1
  fi
  # Only a MEASURED-fresh pool may be read as a deliberate pause (review
  # R5/Agent1): an unmeasurable pool is not evidence of freshness, and an
  # unverifiable pool while off is exactly the "broken monitoring stays green"
  # class.
  if [ "$R2_LIST_OK" != "1" ]; then
    log "kill-switch off and the R2 pool cannot be measured — a deliberate pause cannot be confirmed; filing SWEEP_OFF_STALE (job red)"
    file_alert SWEEP_OFF_STALE "[DR] SWEEP_OFF_STALE — backups off, pool unverifiable" \
      "enabled=false with no config_error, but the R2 pool listing failed (R2_OK=${R2_OK}, R2_LIST_OK=0): pool freshness cannot be established, so this is NOT a confirmed deliberate pause. Check the R2 access key's ListObjects permission." ""
    fail "backups disabled and the R2 pool cannot be measured"
    exit 1
  fi
  resolve_global SWEEP_OFF_STALE "Resolved — the R2 pool is measured fresh."
  # Review P2 (guidance): the app-storage R2_DOWN can only be filed from a
  # disabled path, so without this it would stay open (and its 412 dedup object
  # would swallow the next real recurrence) even after storage recovered.
  if [ "$R2_OK" = "1" ] && [ -z "$STORAGE_ERR" ]; then
    resolve_global R2_DOWN "Resolved — storage answered (R2 preflight OK, storage_error clear)."
  fi
  log "kill-switch: backups deliberately disabled (measured-fresh pool) — skipping"
  finish
fi

# ── 1b. storage error while ENABLED (review P3) ────────────────────────────
# `storage_error` was only filed from the disabled path; an enabled run that
# still reports one is the same broken storage (the sweep is about to fail), so
# surface it here too. §6 must not self-heal it while it is set.
if [ -n "$STORAGE_ERR" ]; then
  log "status reports a storage error while enabled — filing R2_DOWN (job red)"
  file_alert R2_DOWN "[DR] R2_DOWN — app storage unavailable" "status.storage_error: $STORAGE_ERR_SAFE" ""
fi

# ── 2. watcher supervision (WATCHER_DOWN when the daemon is dead) ────────────
# #2843: `.watcher.running // true` is dead — jq's `//` treats a boolean
# false as empty, so a watcher reporting running=false was read as running.
# Review R2b/Agent1: a MISSING/malformed `.watcher` block must not count as
# fresh either (unknown ≠ fresh) — it is measured only with a real boolean.
WATCHER_MEASURED="$(printf '%s' "$STATUS" | jq -r 'if (.watcher.running|type)=="boolean" then "1" else "0" end' 2>/dev/null || echo 0)"
WATCHER_RUNNING="$(printf '%s' "$STATUS" | jq -r 'if (.watcher.running|type)=="boolean" then (.watcher.running|tostring) else "unknown" end' 2>/dev/null || echo unknown)"
WATCHER_AGE="$(printf '%s' "$STATUS" | jq -r 'if (.watcher.age_minutes|type)=="number" then (.watcher.age_minutes|tostring) else "unknown" end' 2>/dev/null || echo unknown)"
WATCHER_STALE=0
if [ "$WATCHER_MEASURED" = "1" ]; then
  watcher_bad=0
  [ "$WATCHER_RUNNING" != "true" ] && watcher_bad=1
  if [ "$WATCHER_AGE" != "unknown" ] && [ "${WATCHER_AGE%.*}" -gt 30 ] 2>/dev/null; then
    watcher_bad=1
  fi
  if [ "$watcher_bad" = "1" ]; then
    WATCHER_STALE=1
    log "watcher heartbeat stale (running=$WATCHER_RUNNING age=${WATCHER_AGE}m) — filing WATCHER_DOWN"
    file_alert WATCHER_DOWN "[DR] WATCHER_DOWN — staleness daemon dead" \
      "The in-process watcher is not reporting (running=$WATCHER_RUNNING, age=${WATCHER_AGE}m). Check app logs." ""
  fi
else
  log "watcher block missing/malformed in /status — cannot assess the watcher; leaving WATCHER_DOWN unchanged"
fi

# ── 3. run the sweep ────────────────────────────────────────────────────────
# curl's own exit code is captured, because a TIMEOUT leaves $RUN EMPTY and an
# empty body is not the same event as an error body. `jq -r '.status // "error"'`
# on ABSENT input prints nothing and exits 0 (the `// "error"` default fires
# only for a present-but-null field), so without this a timeout produced
# RUN_STATUS="" and fell through to the `*)` arm — which files "the sweep backed
# up no team" while the sweep, server-side, has NOT stopped (uvicorn's
# h11 connection_lost only marks the cycle disconnected, and the exempt route
# has no server-side ceiling). Name the shape instead of letting it masquerade.
RUN=""
CURL_RC=0
# #5028: time the call. "driver_timeout" alone hid a 600s-vs-260s inversion —
# the elapsed seconds make budget exhaustion self-evident.
SWEEP_START="$(date +%s)"
RUN="$(curl -sS -m 600 -X POST -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -d '{}' \
  "${API}/v1/internal/backups/sweep" 2>/dev/null)" || CURL_RC=$?
SWEEP_ELAPSED=$(( $(date +%s) - SWEEP_START ))
if [ "$CURL_RC" -eq 28 ]; then
  RUN='{"status":"driver_timeout"}'
elif [ -z "$RUN" ]; then
  RUN='{"status":"empty_response"}'
fi
RUN_STATUS="$(printf '%s' "$RUN" | jq -r 'if type=="object" then (.status // "error") else "error" end' 2>/dev/null || echo error)"
# Security review: RUN_STATUS is app-controlled — never publish it verbatim.
RUN_STATUS_SAFE="$(redact "$RUN_STATUS")"
# Review P2 (bug-deep): `teams_backed_up` counts only DEFAULT-graph backups, so
# a team whose default is legitimately empty while a CUSTOM graph archived
# reads as 0. `graph_totals.backed_up` is the real coverage signal.
GRAPHS_BACKED_UP="$(printf '%s' "$RUN" | jq -r '.graph_totals.backed_up // 0' 2>/dev/null || echo 0)"
[ -n "$GRAPHS_BACKED_UP" ] || GRAPHS_BACKED_UP=0
log "sweep status: $RUN_STATUS_SAFE (took ${SWEEP_ELAPSED}s, curl rc=${CURL_RC})"

# ── 4b. enabled-but-backing-up-nothing (#2823 shape, #2796) ─────────────────
# A sweep that backed up ZERO teams while the R2 pool already holds team
# prefixes is NOT a healthy run — it is the #2823 empty-enumeration class
# (`no_teams`/`no_eligible_teams`/`no_work`, which mean the sweep enumerated
# and found nothing to write). An UNMEASURABLE pool is loud too (unknown ≠
# empty). The chronic 0-team pre-beta state (pool MEASURED empty) stays
# silent: the false-positive envelope requires BOTH surfaces to be empty.
NO_COVERAGE=0
case "$RUN_STATUS" in
  backed_up|degraded)
    ;;
  already_running)
    # The app reports a held lock as a 200 body status=already_running (not an
    # HTTP 202). A lock that is NEVER released looks exactly like a healthy
    # running sweep from this leg, so gate on the sweep's own freshness: a
    # usable last_sweep_at older than the driver-down window means stuck, and
    # an UNVERIFIABLE lock with data in the pool is not healthy either
    # (review R5/R2a/Test).
    last_ts=0
    if [ -n "$LAST_SWEEP_AT" ]; then
      last_ts="$(date -d "$LAST_SWEEP_AT" +%s 2>/dev/null || echo 0)"
    fi
    if [ "$last_ts" != "0" ]; then
      last_age_min=$(( ($(date +%s) - last_ts) / 60 ))
      if [ "$last_age_min" -gt "$DRIVER_DOWN_MIN" ]; then
        log "sweep lock held but the last real sweep was ${last_age_min}m ago (> ${DRIVER_DOWN_MIN}m) — filing SWEEP_NO_COVERAGE (job red)"
        file_alert SWEEP_NO_COVERAGE "[DR] SWEEP_NO_COVERAGE — sweep lock stuck" \
          "the app reported already_running (lock held) and last_sweep.last_sweep_at is ${last_age_min}m old (> ${DRIVER_DOWN_MIN}m). The lock appears stuck; backups are NOT running. Check the app's sweep lock." ""
        NO_COVERAGE=1
      else
        log "sweep lock held; last real sweep ${last_age_min}m ago — healthy"
      fi
    elif [ "$R2_LIST_OK" != "1" ] || [ "${R2_TEAM_COUNT:-0}" -gt 0 ]; then
      # An UNMEASURED pool is never "empty" (review P1, three reviewers):
      # requiring a MEASURED pool here let a stuck lock + failed listing exit 0
      # silently — the exact #2790 class this PR exists to close.
      log "sweep lock held, no usable last_sweep_at, and the pool is not measured-empty (R2_LIST_OK=${R2_LIST_OK}, ${R2_TEAM_COUNT} prefix(es)) — filing SWEEP_NO_COVERAGE (job red)"
      file_alert SWEEP_NO_COVERAGE "[DR] SWEEP_NO_COVERAGE — sweep lock unverifiable" \
        "the app reported already_running (lock held) but /status carries no usable last_sweep.last_sweep_at (the sweep never completed, or ops/state.json is missing), and the R2 pool is NOT measured-empty (R2_LIST_OK=${R2_LIST_OK}, ${R2_TEAM_COUNT} team prefix(es)). The lock state cannot be verified — backups may not be running. Check the app's sweep lock and the R2 access key's ListObjects permission." ""
      NO_COVERAGE=1
    else
      log "sweep lock held; no usable last_sweep_at and the pool is measured empty — leaving silent"
    fi
    ;;
  no_teams|no_eligible_teams|no_work)
    # Review P2 (bug-deep): a team whose DEFAULT graph is legitimately empty
    # while a CUSTOM graph archived reads as 0 teams but real coverage exists.
    if [ "${GRAPHS_BACKED_UP:-0}" -gt 0 ]; then
      log "sweep status=$RUN_STATUS_SAFE but ${GRAPHS_BACKED_UP} graph(s) were backed up — coverage is not zero"
      # Review R1 (P2): coverage EXISTS on this run, so an open
      # SWEEP_NO_COVERAGE must close here — otherwise a recovered pipeline
      # keeps a stale incident open forever and its dedup object absorbs the
      # next genuine occurrence (alert rot). The enabled+stale/empty arms all
      # set NO_COVERAGE=1 and file; this arm is the one covered path.
      resolve_global SWEEP_NO_COVERAGE \
        "Resolved — ${GRAPHS_BACKED_UP} graph(s) backed up (status=$RUN_STATUS_SAFE; default graphs empty, custom graphs covered)."
    elif [ "$R2_LIST_OK" != "1" ]; then
      # Unknown ≠ empty (review R5): the pool could hold teams we cannot see.
      log "sweep backed up 0 teams (status=$RUN_STATUS_SAFE) and the R2 pool could NOT be measured — filing SWEEP_NO_COVERAGE (job red)"
      file_alert SWEEP_NO_COVERAGE "[DR] SWEEP_NO_COVERAGE — enabled, 0 teams, pool unmeasurable" \
        "sweep status=${RUN_STATUS_SAFE} and the R2 pool listing failed (unknown is not empty), so coverage cannot be confirmed. The sweep is enabled but may be backing up nothing (#2823). Check the R2 access key's ListObjects permission and re-run." ""
      NO_COVERAGE=1
    elif [ "${R2_TEAM_COUNT:-0}" -gt 0 ]; then
      log "sweep backed up 0 teams but the R2 pool holds ${R2_TEAM_COUNT} team prefix(es) — filing SWEEP_NO_COVERAGE (job red)"
      file_alert SWEEP_NO_COVERAGE "[DR] SWEEP_NO_COVERAGE — enabled but 0 teams backed up" \
        "sweep status=${RUN_STATUS_SAFE} but the R2 pool holds ${R2_TEAM_COUNT} team prefix(es); last_sweep=${LAST_SWEEP_SAFE}. The sweep is enabled yet backed up 0 teams (#2823) — backups are NOT running." ""
      NO_COVERAGE=1
    else
      log "sweep found 0 teams and the R2 pool is empty — chronic pre-beta state, no incident"
    fi
    ;;
  driver_timeout|empty_response)
    # #4939: the driver's own patience ran out. The sweep is NOT proven stopped —
    # the exempt route has no server-side ceiling and an abandoned request keeps
    # running — so this must not claim "backups are NOT running", and the purge/
    # reconcile ride-along is skipped below (see the ride-along guard) while the
    # sweep may still hold the per-org locks. SWEEP_NO_COVERAGE stays the right
    # KIND: this run produced no coverage, and any run that does back up
    # auto-resolves it. The TEXT is what had to become true.
    log "sweep ${RUN_STATUS_SAFE} (curl rc=${CURL_RC}, took ${SWEEP_ELAPSED}s) — filing SWEEP_NO_COVERAGE with a timeout claim (job red)"
    file_alert SWEEP_NO_COVERAGE "[DR] SWEEP_NO_COVERAGE — sweep did not finish within the driver's budget" \
      "the driver gave up on POST /v1/internal/backups/sweep after ${SWEEP_ELAPSED}s (curl exit ${CURL_RC}, status=${RUN_STATUS_SAFE}). The sweep may still be RUNNING server-side — this leg cannot tell — so this is not evidence that the sweep failed, only that it did not report in time. Last known last_sweep=${LAST_SWEEP_SAFE}." ""
    NO_COVERAGE=1
    ;;
  *)
    # Any other body status (error / enum_failed / unrecognized) means the
    # sweep tried and failed — loud REGARDLESS of pool state (the 0-team
    # envelope applies only to the enumerated-empty statuses above).
    log "sweep did not back up (status=$RUN_STATUS_SAFE) — filing SWEEP_NO_COVERAGE (job red)"
    file_alert SWEEP_NO_COVERAGE "[DR] SWEEP_NO_COVERAGE — enabled but the sweep backed up nothing" \
      "sweep status=${RUN_STATUS_SAFE} (raw: $(redact_truncate "$RUN" 300)). The sweep is enabled but backed up no team — backups are NOT running." ""
    NO_COVERAGE=1
    ;;
esac

# ── 4. trash purge ride-along (#2304, wired #2317) + reconcile ride-along (#654) ──
# Both are skipped when the sweep's own locks may still be unresolved:
# already_running (the lock-holder is running; purge/reconcile would only queue
# behind it) or driver_timeout/empty_response (the pass may still hold the
# per-org locks server-side — see the #4939 note at the guard). Non-2xx is a hard failure for
# reconcile (the cron driver MUST NOT blind the pipeline — a silently skipped
# reconcile step is the same class of silent-no-op that left this endpoint
# uninvoked before #654). The purge erases EXPIRED trash tombstones (> 7-day
# grace) so the runbook's "erased within a day of expiry" claim stays true;
# a purge body of status "errors" (per-tombstone failures) is loud too — the
# per-team lock/retry anchors keep it safe to re-run next hour. We track both
# failures and exit AFTER heartbeat + self-heal so the driver still files
# health signals.
PURGE_FAILED=0
RECONCILE_FAILED=0
# #4939: a timed-out sweep may still hold the per-org locks server-side, so the
# ride-along is skipped for it exactly as for a held lock (already_running).
if [ "$RUN_STATUS" != "already_running" ] \
   && [ "$RUN_STATUS" != "driver_timeout" ] \
   && [ "$RUN_STATUS" != "empty_response" ]; then
  PURGE_RESP="$(mktemp)"
  PURGE_CODE="$(curl -sS -o "$PURGE_RESP" -w '%{http_code}' -m 300 -X POST \
    -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{}' \
    "${API}/v1/internal/backups/purge" 2>/dev/null || echo '000')"
  PURGE_BODY="$(cat "$PURGE_RESP" 2>/dev/null || true)"
  rm -f "$PURGE_RESP"
  PURGE_ST="$(printf '%s' "$PURGE_BODY" | jq -r '.status // "error"' 2>/dev/null || echo error)"
  PURGE_ST_SAFE="$(redact "$PURGE_ST")"
  if [ "$PURGE_CODE" = "200" ] && { [ "$PURGE_ST" = "ok" ] || [ "$PURGE_ST" = "already_running" ]; }; then
    log "purge ride-along OK (status=$PURGE_ST_SAFE teams_purged=$(redact "$(printf '%s' "$PURGE_BODY" | jq -r '.teams_purged // 0')"))"
  else
    # Security review: the purge body is app-controlled and can carry a raw
    # exception string — redact it like the sweep body (it goes to a public log).
    log "purge ride-along FAILED (HTTP $PURGE_CODE status=$PURGE_ST_SAFE): $(redact_truncate "$PURGE_BODY" 300)"
    PURGE_FAILED=1
  fi
  RECONCILE_CODE="$(curl -sS -o /dev/null -w '%{http_code}' -m 120 -X POST \
    -H "Authorization: Bearer $KEY" \
    "${API}/v1/internal/reconcile" 2>/dev/null || echo '000')"
  if [ "$RECONCILE_CODE" -ge 200 ] 2>/dev/null && [ "$RECONCILE_CODE" -lt 300 ]; then
    log "reconcile ride-along OK ($RECONCILE_CODE)"
  else
    log "reconcile ride-along FAILED (HTTP $RECONCILE_CODE)"
    RECONCILE_FAILED=1
  fi
else
  # #5028: name the ACTUAL status. This branch covers three distinct causes;
  # asserting a held lock for all of them sent an investigation after a stale
  # lock that did not exist (0 of 14 sampled runs reported already_running, and
  # /status.lock was null). A non-lock skip is not a lock.
  log "sweep skipped (status=$RUN_STATUS_SAFE) — skipping purge/reconcile ride-along"
fi

# ── 5. driver heartbeat (carries r2_ok so the R2_DOWN signal is auditable) ──
# jq-built body (security review): a `status` string from an arbitrary API
# response must not be interpolated into raw JSON.
r2_ok=$([ "$R2_OK" = "1" ] && echo true || echo false)
curl -sS -m 20 -X POST -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d "$(jq -nc --arg rid "$(date +%s)" --arg s "$RUN_STATUS_SAFE" --argjson ok "$r2_ok" '{run_id:$rid,status:$s,r2_ok:$ok}')" \
  "${API}/v1/internal/driver/heartbeat" >/dev/null 2>&1 || true

# ── 6. self-heal ────────────────────────────────────────────────────────────
# Evidence-tiered, because a no-coverage sweep does NOT prove the same things
# as a real backup:
#   * APP_DOWN + the resolved SWEEP_CONFIG_ERROR/SWEEP_OFF_STALE clear on ANY
#     completed /status + sweep round trip — the app answered.
#   * R2_DOWN clears only when the driver's OWN head-bucket preflight passed
#     this run (evidence, not inference).
#   * WATCHER_DOWN clears on WATCHER evidence: a fresh .watcher heartbeat read
#     this run. #2796: the pre-fix code treated `no_teams` as healthy and
#     closed a WATCHER_DOWN it had just filed because it keyed off the sweep
#     status instead of the heartbeat.
#   * SWEEP_NO_COVERAGE clears only on a run that actually backed up.
# #2411: "degraded" (per-graph errors with ≥1 graph backed up) still PROVES
# the watcher/R2 are up — it self-heals like backed_up.
case "$RUN_STATUS" in
  backed_up|degraded|no_teams|no_eligible_teams|no_work) SWEEP_COMPLETED=1 ;;
  *) SWEEP_COMPLETED=0 ;;
esac
if [ "$SWEEP_COMPLETED" = "1" ]; then
  for kind in APP_DOWN SWEEP_CONFIG_ERROR SWEEP_OFF_STALE; do
    resolve_global "$kind" "Resolved — the app answered (/status + sweep completed, status=$RUN_STATUS_SAFE)."
  done
  # R2_DOWN is cleared only when the driver's OWN storage probe succeeded this
  # run (review R1/R3): a sweep that "completes" with R2_OK=0 (head-bucket
  # failed) must not close the R2_DOWN it just filed — that would re-file and
  # re-close on every run forever. Review R3: a non-null storage_error also
  # proves the app-side storage is still broken — never close on that run.
  if [ "$R2_OK" = "1" ] && [ -z "$STORAGE_ERR" ] && [ "$R2_LIST_OK" = "1" ]; then
    resolve_global R2_DOWN "Resolved — the storage answered (R2 preflight + listing OK + sweep completed, status=$RUN_STATUS_SAFE)."
  else
    log "self-heal: R2/storage evidence is not clean this run — leaving R2_DOWN open"
  fi
fi
# WATCHER_DOWN self-heals only on WATCHER EVIDENCE (a real boolean .watcher
# block read this run) — review R3 + R2b: a missing/malformed block is not
# evidence the daemon is alive, so it must not close an open WATCHER_DOWN.
if [ "$WATCHER_MEASURED" = "1" ] && [ "$WATCHER_STALE" != "1" ]; then
  resolve_global WATCHER_DOWN "Resolved — the watcher heartbeat is fresh."
elif [ "$WATCHER_MEASURED" != "1" ]; then
  log "self-heal: watcher block unmeasurable — leaving WATCHER_DOWN unchanged"
fi
if [ "$RUN_STATUS" = "backed_up" ] || [ "$RUN_STATUS" = "degraded" ]; then
  resolve_global SWEEP_NO_COVERAGE "Resolved — sweep succeeded ($RUN_STATUS_SAFE)."
  log "self-heal: closed open incidents for a healthy run"
fi

if [ "$PURGE_FAILED" = "1" ]; then
  fail "purge ride-along failed — investigate (expired trash not erased)"
  exit 1
fi

if [ "$RECONCILE_FAILED" = "1" ]; then
  fail "reconcile ride-along failed — investigate"
  exit 1
fi

if [ "$NO_COVERAGE" = "1" ]; then
  fail "sweep backed up nothing (status=$RUN_STATUS_SAFE) — see the SWEEP_NO_COVERAGE incident"
  exit 1
fi
log "done"
finish
