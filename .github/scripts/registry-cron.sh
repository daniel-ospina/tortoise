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
# broken — i.e. whenever an incident was filed this run, not only on the four
# kill-switch/no-coverage states. `file_alert` sets LOUD and every terminal
# exit goes through finish(). Silent ⟺ nothing was filed this run.
LOUD=0
finish() {
  if [ "${LOUD:-0}" = "1" ]; then
    log "loud run: an incident was filed — exiting RED"
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
  # `global` is this driver's pre-#2844 spelling of a subject-less incident;
  # both spellings now canonicalize to `_` (the AlertStore's spelling), so the
  # two writers share ONE create-once point.
  if [ -z "$id" ] || [ "$id" = "global" ]; then id="_"; fi
  printf 'ops/alerts/%s/%s.json\n' "$kind" "$id"
}
alert_keys_all() { # kind id -> the canonical key + every legacy spelling
  local kind="$1" id="${2:-}"
  alert_key "$kind" "$id"
  if [ -z "$id" ] || [ "$id" = "global" ] || [ "$id" = "_" ]; then
    printf 'ops/alerts/%s/global.json\n' "$kind"
  fi
}
r2_put_once() { # key body_file
  aws s3api put-object --endpoint-url "$R2_ENDPOINT" \
    --bucket "$R2_BUCKET" --key "$1" --body "$2" --if-none-match "*" >/dev/null 2>&1
}
r2_get() { # key -> body (empty on failure)
  aws s3api get-object --endpoint-url "$R2_ENDPOINT" --bucket "$R2_BUCKET" --key "$1" /dev/stdout 2>/dev/null || true
}
r2_delete() { # key — delete-to-resolve (the alert_store lifecycle contract)
  aws s3api delete-object --endpoint-url "$R2_ENDPOINT" --bucket "$R2_BUCKET" --key "$1" >/dev/null 2>&1 || true
}
gh_find_open() { # kind id(subject) -> first open issue number whose TITLE subject matches exactly (or empty)
  # #2375: subject-scoped — a bare kind search lets a per-graph issue
  # ("[DR] STALE — team_a:g_x") be adopted by a team-level file ("… team_a")
  # and vice versa (the bare team subject is a PREFIX of the per-graph
  # subject); recovery then closes the WRONG issue and orphans its dedup
  # object (silent-loss cross-talk — the server side is subject-scoped since
  # #2313; the driver must match). Global alerts (id="global") keep the
  # kind-only match (their titles carry prose, not the id).
  [ -n "$GH_TOKEN" ] || return 0
  local kind="$1" id="${2:-}"
  if [ "$id" = "global" ] || [ -z "$id" ]; then
    curl -sS -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
      "https://api.github.com/search/issues?q=repo:${REPO}+is:issue+is:open+label:%22dr:backup%22+in:title+%22%5BDR%5D+$kind%22" \
      | jq -r '.items[0].number // empty' 2>/dev/null || true
    return 0
  fi
  curl -sS -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/search/issues?q=repo:${REPO}+is:issue+is:open+label:%22dr:backup%22+in:title+%22%5BDR%5D+$kind%22&per_page=20" \
    | jq -r --arg suf " — $id" \
      '[.items[] | select(.title | endswith($suf))][0].number // empty' 2>/dev/null || true
}
gh_issue_open() { # number -> 0 when OPEN or unknown; 1 when confirmed closed OR missing
  # #2796 (review R3/R4): the 412 dedup branch must trust the object over a GH
  # search, and must be able to tell a DELETED issue (404) from a transient
  # blip. A 404 is definitively gone → re-file (otherwise the recurrence is
  # swallowed forever). Rate-limit/5xx/network → assume OPEN, never duplicate.
  local n="${1:-}" code="" state=""
  [ -n "$GH_TOKEN" ] && [ -n "$n" ] || return 0
  code="$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $GH_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPO}/issues/${n}" 2>/dev/null || echo 000)"
  case "$code" in
    404) return 1 ;;   # definitively missing → re-file
    200) : ;;
    *)   return 0 ;;   # transport / rate-limit / 5xx → assume open
  esac
  state="$(curl -sS -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPO}/issues/${n}" | jq -r '.state // empty' 2>/dev/null || true)"
  case "$state" in
    open) return 0 ;;
    "")   return 0 ;;   # unparseable body on a 200 → assume open
    *)    return 1 ;;   # closed
  esac
}
gh_close() { # number comment kind id
  [ -n "$GH_TOKEN" ] || return 0
  local kind="${3:-}" id="${4:-}"
  curl -sS -X POST -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPO}/issues/$1/comments" \
    -d "$(jq -nc --arg b "$2" '{body:$b}')" >/dev/null 2>&1 || true
  curl -sS -X PATCH -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPO}/issues/$1" -d '{"state":"closed"}' >/dev/null 2>&1 || true
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
}
resolve_global() { # kind comment — close an open global incident (no-op if none)
  local kind="$1" comment="$2" num="" owner=""
  # #3127: refuse to clear a kind this driver has no evidence for. Without this
  # the driver's generic sweep-completed self-heal would close incidents the
  # driver never observed recovering.
  owner="$(kind_owner "$kind")"
  if [ "$owner" != "driver" ] && [ "$owner" != "unspecified" ]; then
    # Provenance exception (#3127): the driver may always clear a sentinel it
    # opened itself — its own probes observed the condition being cleared.
    _w="$(printf '%s' "$(r2_get "$(alert_key "$kind" "global")")" | jq -r '.writer // empty' 2>/dev/null || true)"
    if [ "$_w" != "driver" ]; then
      log "self-heal: refusing to close ${kind} — it is owned by the ${owner}, whose probes cover its recovery condition"
      return 0
    fi
  fi
  num="$(gh_find_open "$kind" "global")"
  if [ -n "$num" ]; then gh_close "$num" "$comment" "$kind" "global"; fi
}
telegram() { # text
  [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] \
    && curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" --data-urlencode "text=$1" >/dev/null 2>&1 || true
}
file_alert() { # kind title body dedup_id
  local kind="$1" title="$2" body="$3" id="$4" num="" tmp="" issue_num="" key="" filed=0
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
      rm -f "$tmp"
      return 0
    fi
  done < <(alert_keys_all "$kind" "$id")
  printf '{"kind":"%s","issue_number":null,"filed_at":"%s","writer":"driver"}' "$kind" "$(date -u +%FT%TZ)" > "$tmp"
  if r2_put_once "$key" "$tmp"; then
    num="$(gh_find_open "$kind" "$id")"
    if [ -z "$num" ]; then
      num="$(curl -sS -X POST -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/${REPO}/issues" \
        -d "$(jq -nc --arg t "$title" --arg b "$body" '{title:$t, body:$b, labels:["dr:backup"]}')" \
        | jq -r '.number // empty' 2>/dev/null || true)"
      [ -n "$num" ] && filed=1
    fi
    if [ -n "$num" ]; then
      # Review F7 (coherence): backfill the AUTHORITATIVE R2 object with the
      # issue number here too. Without it the object keeps issue_number=null
      # until the next run, so a transient empty GitHub search in that window
      # would create a duplicate (the 412 object-trust path cannot help).
      # Provenance is claimed ONLY when this driver created the issue (#3127):
      # stamping an ADOPTED issue as driver-filed is the exact defect the store
      # fixed in Python, and `resolve_global` treats this field as authority.
      # telegram_pushed records that the announcement happened, so the store
      # does not re-announce an issue this driver already showed a human.
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
      rm -f "$tmp"
      return 0
    fi
    num="$(gh_find_open "$kind" "$id")"
    if [ -z "$num" ]; then
      num="$(curl -sS -X POST -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/${REPO}/issues" \
        -d "$(jq -nc --arg t "$title" --arg b "$body" '{title:$t, body:$b, labels:["dr:backup"]}')" \
        | jq -r '.number // empty' 2>/dev/null || true)"
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
    "R2 preflight (head-bucket) failed from the driver. Runbook: docs/ops/registry-backup-dr.md" "global"
fi

if [ "$R2_OK" = "1" ]; then
  if TEAMS="$(aws s3api list-objects-v2 --endpoint-url "$R2_ENDPOINT" \
    --bucket "$R2_BUCKET" --prefix "backups/" --delimiter "/" --query "CommonPrefixes[].Prefix" \
    --output text 2>/dev/null)"; then
    R2_LIST_OK=1
  else
    TEAMS=""
    log "R2 top-level listing failed — pool state is UNKNOWN (not empty)"
  fi
  if [ -n "$TEAMS" ]; then
    # Review F5 (security): a predictable /tmp path is a symlink/overwrite
    # hazard on a shared runner and can be read back stale. Use mktemp.
    IDX_ERR="$(mktemp)"
    # `while read` rather than `for $TEAMS`: an unquoted expansion word-splits
    # AND glob-expands, so a bucket key containing `*` or whitespace would
    # fabricate team names carried into R2 keys and incident titles (security
    # review). Skip keys that are not our validated team-id shape.
    while IFS= read -r prefix; do
      [ -n "$prefix" ] || continue
      team="$(basename "$prefix")"
      case "$team" in
        ''|*[!A-Za-z0-9_-]*)
          log "skipping unexpected team prefix '${prefix}' (not a valid team id)"
          continue
          ;;
      esac
      R2_TEAM_COUNT=$((R2_TEAM_COUNT + 1))
      # #2375: DEFAULT-graph freshness ONLY — nested default segment
      # (backups/{team}/default/) + legacy flat (pre-#2313 default dumps;
      # flat keys start with the dump year "2xxx" so the 2-prefix never
      # matches custom nested gids g_*). The pre-#2375 leg took the newest
      # dump.enc under the WHOLE team prefix, so a team whose DEFAULT failed
      # for > STALE_MIN while a custom graph backed up fresh read "not
      # stale" — exactly the app-down case this leg exists to cover. A
      # legacy-flat classification index (#2370) additionally excludes
      # C5-era custom flat dumps when present (flat-only fallback is parity
      # pre-index).
      team_measured=1
      if ! newest="$(aws s3api list-objects-v2 --endpoint-url "$R2_ENDPOINT" \
        --bucket "$R2_BUCKET" --prefix "backups/${team}/default/" \
        --query "Contents[?ends_with(Key, 'dump.enc')] | sort_by(@, &LastModified) | [-1].LastModified" \
        --output text 2>/dev/null)"; then
        newest=""
        team_measured=0
        log "team ${team}: default-archive listing FAILED — freshness UNKNOWN"
      fi
      if ! flat_list="$(aws s3api list-objects-v2 --endpoint-url "$R2_ENDPOINT" \
        --bucket "$R2_BUCKET" --prefix "backups/${team}/2" \
        --query "Contents[?ends_with(Key, 'dump.enc')].[Key,LastModified]" \
        --output json 2>/dev/null)"; then
        flat_list="[]"
        team_measured=0
        log "team ${team}: legacy-flat listing FAILED — freshness UNKNOWN"
      fi
      if [ "$team_measured" = "0" ]; then
        # A failed per-team read is unmeasurable, never "no archive" (review
        # R2a): the disabled path must refuse to claim freshness, not file a
        # false stale.
        R2_LIST_OK=0
        continue
      fi
      if [ -n "$flat_list" ] && [ "$flat_list" != "[]" ]; then
        # Review P2 (bug-deep): a FAILED read of the classification index is
        # NOT "no index" — a custom flat could then be misread as a default
        # dump and mask a stale default. Only a definitive absence
        # (NoSuchKey/404) means the pre-#2370 parity.
        idx=""; idx_rc=0
        idx="$(aws s3api get-object --endpoint-url "$R2_ENDPOINT" --bucket "$R2_BUCKET" \
          --key "ops/legacy-flat-index/${team}.json" /dev/stdout 2>"$IDX_ERR")" || idx_rc=$?
        if [ "$idx_rc" != "0" ] && ! grep -qiE 'NoSuchKey|404|Not Found' "$IDX_ERR" 2>/dev/null; then
          log "team ${team}: legacy-flat index read FAILED — freshness UNKNOWN"
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
          log "team ${team}: unparseable archive timestamp '${newest}' — treating pool as stale"
          POOL_STALE=1
        else
          age_min=$(( ($(date +%s) - newest_ts) / 60 ))
          if [ "$age_min" -gt "$STALE_MIN" ]; then
            log "team ${team}: newest archive ${age_min}m old — filing STALE (direct leg)"
            file_alert STALE "[DR] STALE — ${team}" "Direct R2 freshness check: newest archive ${age_min}m old (> ${STALE_MIN}m)." "$team"
          fi
          if [ "$age_min" -gt "$DRIVER_DOWN_MIN" ]; then
            POOL_STALE=1
          fi
        fi
      else
        # Team prefix present but no DEFAULT (or legacy-flat) archive: this
        # graph has NO restorable dump. Never-backed-up is worse than old, so
        # a disabled sweep must not read this as a fresh pool (#2796 review R5).
        log "team ${team}: team prefix present but no default archive — treating pool as stale"
        POOL_STALE=1
      fi
    done < <(printf '%s\n' "$TEAMS" | tr '\t' '\n')
    rm -f "$IDX_ERR"
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
    "head-bucket succeeded but one or more list-objects-v2 calls failed (R2_LIST_OK=0). The pool cannot be measured, so archive freshness and coverage cannot be verified. Check the R2 access key's ListObjects permission." "global"
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
    "The hosted API did not answer /status. Runbook: docs/ops/registry-backup-dr.md" "global"
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
log "raw status: $(redact_truncate "$STATUS" 600)"

if [ "$ENABLED" = "unknown" ]; then
  # Fail CLOSED, never silent: an unclassifiable /status must not look like a
  # deliberate pause.
  log "unparseable /status (no boolean .enabled) — filing SWEEP_NO_COVERAGE (job red)"
  file_alert SWEEP_NO_COVERAGE "[DR] SWEEP_NO_COVERAGE — /status unclassifiable" \
    "GET /status returned 200 but carried no boolean .enabled field (schema drift, or a non-JSON body). The driver fails CLOSED rather than reading an unknown shape as a deliberate pause. Check the app version and the /status contract." "global"
  fail "unparseable /status — no boolean .enabled field"
  exit 1
fi

if [ "$ENABLED" != "true" ]; then
  # #2796: enabled:false conflates four states. Only a genuine deliberate
  # pause (no config error, no storage error, fresh pool) may exit silently.
  if [ -n "$CONFIG_ERR" ]; then
    log "kill-switch is NOT an operator decision — config_error is set; filing SWEEP_CONFIG_ERROR (job red)"
    file_alert SWEEP_CONFIG_ERROR "[DR] SWEEP_CONFIG_ERROR — backups off, config broken" \
      "enabled=false with config_error: ${CONFIG_ERR_SAFE}. The sweep flag says 'run' but load_config() raised, so nothing can be written. Fix the Fly secret/config (runbook: docs/ops/registry-backup-dr.md §REGISTRY_STREAM_KEY), then re-run." "global"
    fail "sweep disabled by a configuration error: ${CONFIG_ERR_SAFE}"
    exit 1
  fi
  # Config is readable again (config_error is null here) — clear the incident.
  resolve_global SWEEP_CONFIG_ERROR "Resolved — load_config() no longer raises."
  if [ -n "$STORAGE_ERR" ]; then
    log "status reports a storage error — filing R2_DOWN (not a kill-switch)"
    file_alert R2_DOWN "[DR] R2_DOWN — app storage unavailable" "status.storage_error: $STORAGE_ERR_SAFE" "global"
    fail "backups disabled by a storage error: ${STORAGE_ERR_SAFE}"
    exit 1
  fi
  if [ "$POOL_STALE" = "1" ]; then
    log "kill-switch while the R2 pool is stale (oldest > ${DRIVER_DOWN_MIN}m) — filing SWEEP_OFF_STALE (job red)"
    file_alert SWEEP_OFF_STALE "[DR] SWEEP_OFF_STALE — backups off and the pool is stale" \
      "enabled=false with no config_error, but the direct-R2 leg found a default archive older than ${DRIVER_DOWN_MIN}m (or a team with none at all) among ${R2_TEAM_COUNT} team prefix(es). An intentional pause must not let the pool decay unnoticed: re-enable backups or declare a bounded pause." "global"
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
      "enabled=false with no config_error, but the R2 pool listing failed (R2_OK=${R2_OK}, R2_LIST_OK=0): pool freshness cannot be established, so this is NOT a confirmed deliberate pause. Check the R2 access key's ListObjects permission." "global"
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
  file_alert R2_DOWN "[DR] R2_DOWN — app storage unavailable" "status.storage_error: $STORAGE_ERR_SAFE" "global"
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
      "The in-process watcher is not reporting (running=$WATCHER_RUNNING, age=${WATCHER_AGE}m). Check app logs." "global"
  fi
else
  log "watcher block missing/malformed in /status — cannot assess the watcher; leaving WATCHER_DOWN unchanged"
fi

# ── 3. run the sweep ────────────────────────────────────────────────────────
RUN="$(curl -sS -m 600 -X POST -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -d '{}' \
  "${API}/v1/internal/backups/sweep" 2>/dev/null || true)"
RUN_STATUS="$(printf '%s' "$RUN" | jq -r '.status // "error"' 2>/dev/null || echo error)"
# Security review: RUN_STATUS is app-controlled — never publish it verbatim.
RUN_STATUS_SAFE="$(redact "$RUN_STATUS")"
# Review P2 (bug-deep): `teams_backed_up` counts only DEFAULT-graph backups, so
# a team whose default is legitimately empty while a CUSTOM graph archived
# reads as 0. `graph_totals.backed_up` is the real coverage signal.
GRAPHS_BACKED_UP="$(printf '%s' "$RUN" | jq -r '.graph_totals.backed_up // 0' 2>/dev/null || echo 0)"
[ -n "$GRAPHS_BACKED_UP" ] || GRAPHS_BACKED_UP=0
log "sweep status: $RUN_STATUS_SAFE"

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
          "the app reported already_running (lock held) and last_sweep.last_sweep_at is ${last_age_min}m old (> ${DRIVER_DOWN_MIN}m). The lock appears stuck; backups are NOT running. Check the app's sweep lock." "global"
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
        "the app reported already_running (lock held) but /status carries no usable last_sweep.last_sweep_at (the sweep never completed, or ops/state.json is missing), and the R2 pool is NOT measured-empty (R2_LIST_OK=${R2_LIST_OK}, ${R2_TEAM_COUNT} team prefix(es)). The lock state cannot be verified — backups may not be running. Check the app's sweep lock and the R2 access key's ListObjects permission." "global"
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
        "sweep status=${RUN_STATUS_SAFE} and the R2 pool listing failed (unknown is not empty), so coverage cannot be confirmed. The sweep is enabled but may be backing up nothing (#2823). Check the R2 access key's ListObjects permission and re-run." "global"
      NO_COVERAGE=1
    elif [ "${R2_TEAM_COUNT:-0}" -gt 0 ]; then
      log "sweep backed up 0 teams but the R2 pool holds ${R2_TEAM_COUNT} team prefix(es) — filing SWEEP_NO_COVERAGE (job red)"
      file_alert SWEEP_NO_COVERAGE "[DR] SWEEP_NO_COVERAGE — enabled but 0 teams backed up" \
        "sweep status=${RUN_STATUS_SAFE} but the R2 pool holds ${R2_TEAM_COUNT} team prefix(es); last_sweep=${LAST_SWEEP_SAFE}. The sweep is enabled yet backed up 0 teams (#2823) — backups are NOT running." "global"
      NO_COVERAGE=1
    else
      log "sweep found 0 teams and the R2 pool is empty — chronic pre-beta state, no incident"
    fi
    ;;
  *)
    # Any other body status (error / enum_failed / unrecognized) means the
    # sweep tried and failed — loud REGARDLESS of pool state (the 0-team
    # envelope applies only to the enumerated-empty statuses above).
    log "sweep did not back up (status=$RUN_STATUS_SAFE) — filing SWEEP_NO_COVERAGE (job red)"
    file_alert SWEEP_NO_COVERAGE "[DR] SWEEP_NO_COVERAGE — enabled but the sweep backed up nothing" \
      "sweep status=${RUN_STATUS_SAFE} (raw: $(redact_truncate "$RUN" 300)). The sweep is enabled but backed up no team — backups are NOT running." "global"
    NO_COVERAGE=1
    ;;
esac

# ── 4. trash purge ride-along (#2304, wired #2317) + reconcile ride-along (#654) ──
# Both are skipped when the sweep reported already_running (the lock-holder is
# running; purge/reconcile would only queue behind it). Non-2xx is a hard failure for
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
if [ "$RUN_STATUS" != "already_running" ]; then
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
  log "sweep reported already_running (lock held) — skipping purge/reconcile to avoid racing a restore"
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
