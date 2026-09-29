#!/usr/bin/env bash
# ============================================================================
# auto-file-issue.sh — the SHARED auto-file substrate (#3907).
#
# WHY THIS EXISTS
#   Every monitor here (the DR driver, the availability watchdog, the
#   welcome-e2e monitor, the e2e-live reconcile, the tenant-provision log
#   monitor) files a GitHub issue when its probe fails. Written inline per
#   monitor, that step drifted into THREE different behaviours:
#     1. file a NEW issue on every run (the original welcome-e2e defect #2706 —
#        the title embedded `run ${github.run_id}`, so 59 near-identical issues
#        accumulated);
#     2. search first, then SILENTLY DROP the recurrence (the tenant-provision
#        monitor: "monitor issue already open — not duplicating");
#     3. search first, then COMMENT (the welcome-e2e monitor after #2706).
#   (1) is duplicate spam; (2) is the same defect inverted — it hides the
#   escalation, so a monitor firing at cron rate on one unchanged fault reads as
#   a single quiet issue, and "a check that fires constantly is a check nobody
#   reads" becomes "a check that fires constantly is a check nobody SEES".
#   #3907 names both halves: file the FINDING, not the OCCURRENCE — one issue,
#   with every occurrence recorded on it.
#
# THE CONTRACT
#   `file --title <stable-key> --body-file <path> [--label <label>]`
#     * search OPEN issues for EXACTLY <stable-key>, authored by GitHub Actions;
#     * found  → post `Recurrence #N` on it (N = existing occurrence comments+1);
#     * absent → create the issue titled <stable-key> from the body file;
#     * search FAILS → REFUSE to file, exit non-zero (never risk a duplicate:
#       the red run IS the alert — #2706/#2140 — and a failed search cannot tell
#       "no incident" from "cannot see incidents").
#   <stable-key> is BOTH the issue title AND the dedupe key, so it must NOT
#   embed a run id, a timestamp, or a count. Everything that varies per
#   occurrence belongs in the BODY (which is rewritten on the issue only when
#   the issue is first filed; later detail arrives as recurrence comments).
#
# THE TOKEN MUST BE THE ACTIONS TOKEN (not a PAT, not a fine-grained token)
#   The dedupe search keys on `author:app/github-actions` and the adopt check on
#   the reserved login `github-actions[bot]`. A PAT (or any token that is not
#   the Actions app token) files and searches as ITS OWNER, so the search never
#   matches, `total` reads 0 with no warning, and a FRESH issue is filed on
#   EVERY run — the #2706 duplicate spam this substrate exists to prevent, and
#   silent. #5019 migrates four more monitors onto this substrate, so the trap
#   only widens. The AUTHOR is therefore ASSERTED, and the misuse fails loudly
#   instead of degenerating.
#
# ASSERT THE AUTHOR FROM THE WRITE'S OWN RESPONSE — never probe the actor
#   `POST /issues` and `POST /issues/{n}/comments` both return the created
#   object's `user.login`, so the write itself says who made it. That is exact,
#   costs NO extra call, and cannot disagree with what actually happened.
#   ⛔ An earlier round probed the actor with `gh api user` instead, and that was
#   a P0: GET /user is NOT available to a GitHub App INSTALLATION token, and
#   GITHUB_TOKEN *is* one (GitHub's GET /user docs list fine-grained USER tokens
#   and App USER tokens, not installation tokens; the live failure is
#   actions/runner#3289 → `Resource not accessible by integration`). Under a real
#   Actions run the probe 403'd, the assertion returned non-zero, and the
#   substrate REFUSED BEFORE SEARCHING — filing nothing, ever, on both adopters,
#   so #3907's acceptance was unreachable in production. It shipped green only
#   because the unit-test stub FABRICATED a `github-actions[bot]` answer that
#   real GitHub never returns; the stub now models the real 403.
#   Fail-closed: an unparseable or non-reserved login fails the run.
#
# SECURITY — the title is attacker-reachable
#   This repo is PUBLIC, so any account can open an issue whose title matches a
#   key. If such an issue were adopted, the monitor would comment on a
#   stranger's thread AND never create its own tracker (a permanent, attacker-
#   controlled blind spot). Two-part guard, mirroring welcome-e2e-monitor.sh:
#     1. the search carries `author:app/github-actions`, AND
#     2. the returned item is re-checked for the RESERVED login
#        `github-actions[bot]` AND for an EXACT title match.
#   `user.type == "Bot"` is NOT a boundary: `renovate[bot]`/`dependabot[bot]`
#   are also bots (welcome-e2e-monitor.sh round 4). The EXACT-title check is a
#   second, independent guard: `in:title "…"` is a word match, so it can return
#   a look-alike whose title merely contains the key.
#   A look-alike that is not ours is IGNORED; a fresh machine issue is filed.
#
# Test: bash .github/scripts/auto-file-issue.test.sh
# ============================================================================
set -euo pipefail

REPO="${GITHUB_REPOSITORY:-daniel-ospina/tortoise}"
RUN_URL="${GITHUB_SERVER_URL:-https://github.com}/${REPO}/actions/runs/${GITHUB_RUN_ID:-0}"

# The only author whose issue this substrate may adopt or comment on. GitHub
# reserves the `[bot]` suffix and the `github-actions` app name, so a normal
# account cannot spoof it.
AUTO_FILE_BOT_LOGIN="github-actions[bot]"
# Hidden marker identifying OUR occurrence comments, so the recurrence number is
# derived from the issue itself (the visible, durable record) instead of from a
# local counter that a re-run or a rewritten dedup object could reset.
AUTO_FILE_MARKER="<!-- auto-file-occurrence -->"
# The counting walk is BOUNDED: 20 pages x 100 comments. An incident with more
# recurrences than that is already far past the point where the exact number
# matters, and the bound keeps a pathological thread from hanging the cron.
AUTO_FILE_MAX_PAGES="${AUTO_FILE_MAX_PAGES:-20}"

af_err()  { echo "::error::$*" >&2; }
af_warn() { echo "::warning::$*" >&2; }
af_note() { echo "::notice::$*"; }

af_token() { printf '%s' "${GH_TOKEN:-${GITHUB_TOKEN:-}}"; }
af_have_gh() { command -v gh >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; }

# Assert the EFFECTIVE AUTHOR from a WRITE'S OWN RESPONSE. The response's
# `user.login` IS the authority: it cannot disagree with what was written, and
# it costs no extra call (unlike the `gh api user` probe this replaces, which
# an installation token cannot answer at all). A PAT would write as its own
# owner, so the `author:app/github-actions` dedupe search never matches and
# every run risks a NEW issue — the #2706 duplicate spam, silently. Fail-closed:
# an unparseable or non-reserved login refuses.
af_actor_ok() { # <write-response-json>
  local resp="${1:-}" login bot
  login="$(printf '%s' "$resp" | jq -r '.user.login // empty' 2>/dev/null || true)"
  bot="$(printf '%s' "$AUTO_FILE_BOT_LOGIN" | tr '[:upper:]' '[:lower:]')"
  if [ "$(printf '%s' "$login" | tr '[:upper:]' '[:lower:]')" = "$bot" ]; then
    return 0
  fi
  if [ -z "$login" ]; then
    af_err "a GitHub write response carried no user.login — cannot verify the author; refusing to continue (the dedupe search keys on author:app/github-actions, so an unverified author can file a DUPLICATE on every run)"
  else
    af_err "the write was authored by '${login}', not the reserved '${AUTO_FILE_BOT_LOGIN}' — this substrate REQUIRES the GitHub Actions token (secrets.GITHUB_TOKEN). A PAT (or any non-Actions token) writes as its own owner, so the 'author:app/github-actions' dedupe search never matches and EVERY run risks a duplicate issue (#2706). Point GH_TOKEN at the Actions token, and remove the issue this run created as '${login}'."
  fi
  return 1
}

# Echoes a POSITIVE integer issue number, "" when NO machine-authored issue with
# this EXACT title is open, or "__ERR__" when the search failed or answered
# something unparseable. __ERR__ must NEVER be read as "none" — that is the
# duplicate-spam direction (and the caller refuses to file on it).
af_open_issue() { # <stable-title>
  local title="$1" q enc out n total
  # The query MUST go in the URL path (`gh api -f q=…` switches the method to
  # POST and 404s on this endpoint — tenant-provision-monitor, #1133).
  # Deliberately NO `label:` filter: a renamed or deleted label would silently
  # empty the search and turn the monitor back into a duplicate spammer.
  q="repo:${REPO} is:issue is:open in:title author:app/github-actions \"$title\""
  enc="$(printf '%s' "$q" | jq -sRr @uri)"
  # PAGED, mirroring availability-watchdog.sh (the implementation this substrate
  # generalizes). `per_page=100 --paginate` walks every page — GitHub's own
  # 1000-result cap bounds it — so search ranking (relevance-based, NOT
  # equality-first) can never push the real exact-match off page 1 and turn the
  # monitor back into a duplicate filer. `jq -rs` slurps the concatenated page
  # docs before the select.
  if ! out="$(gh api "search/issues?q=${enc}&per_page=100" --paginate 2>/dev/null)"; then
    af_err "issue search failed — refusing to file a possible duplicate; this failing run IS the alert"
    printf '__ERR__'
    return 0
  fi
  # The select is the load-bearing guard: reserved LOGIN *and* EXACT title. An
  # empty item list ("no incident") is NOT a failure — only an unparseable
  # answer is (conflating the two makes the monitor refuse to file on the very
  # first outage).
  if ! n="$(printf '%s' "$out" | jq -rs \
      --arg login "$AUTO_FILE_BOT_LOGIN" --arg title "$title" \
      '[.[].items[]? | select((.user.login // "") == $login) | select((.title // "") == $title)][0].number // empty' \
      2>/dev/null)"; then
    af_err "issue search returned an unparseable body"
    printf '__ERR__'
    return 0
  fi
  if [ -z "$n" ]; then
    # Distinguish "nothing matched" from "something matched but was NOT ours":
    # the latter is the forged-title case, and it is worth a loud line.
    total="$(printf '%s' "$out" | jq -rs '[.[].items[]?] | length' 2>/dev/null || true)"
    case "$total" in
      ''|*[!0-9]*) total=0 ;;
    esac
    if [ "$total" -gt 0 ]; then
      af_warn "issue search matched ${total} open issue(s) but NONE was an EXACT machine-authored '${title}' — ignoring the look-alike(s) and filing a fresh machine issue"
    fi
    printf ''
    return 0
  fi
  case "$n" in
    *[!0-9]*|0) printf '__ERR__'; return 0 ;;
  esac
  printf '%s' "$n"
}

# How many occurrence comments does this issue already carry? Counted from the
# issue (the durable, human-visible record), never from a local file.
#
# AUTHOR-FILTERED. The marker is not secret — the public comments API returns it
# verbatim on every recurrence comment — so a bare `contains($m)` let ANY third
# party inflate the count by posting comments that carry the marker. The count
# would then jump (one real recurrence + three outsider markers → `Recurrence
# #5`), and the recurrence number is the ONE field of #3907 an outsider can
# corrupt. Filtering on the reserved bot LOGIN, exactly as the dedupe search
# does, closes the over-count direction.
#
# A failed or unparseable page returns the count SO FAR: the counter may
# UNDER-report (the next comment re-states a lower N — harmless) but it can
# never be inflated by a third party, and it never fabricates a number.
#
# MAGNITUDE: recurrence comments carry the run URL, not a re-stated magnitude.
# The magnitude is written ONCE in the issue body at first filing, so a material
# escalation (1 → 10,000) is not re-stated on later comments — the recurrence
# COUNT is the escalation signal, and the latest run URL links the detail. This
# is deliberate (#3907: file the finding, not the occurrence — re-posting the
# full body each run would re-introduce the hourly spam the dedupe removes).
af_occurrence_count() { # <issue-number> -> integer
  local n="${1:-}" page=1 total=0 resp cnt len
  [ -n "$n" ] || { printf '0'; return 0; }
  while [ "$page" -le "$AUTO_FILE_MAX_PAGES" ]; do
    if ! resp="$(gh api "repos/${REPO}/issues/${n}/comments?per_page=100&page=${page}" 2>/dev/null)"; then
      printf '%s' "$total"; return 0
    fi
    cnt="$(printf '%s' "$resp" | jq -r --arg m "$AUTO_FILE_MARKER" --arg login "$AUTO_FILE_BOT_LOGIN" \
      '[.[]? | select(((.user.login // "") == $login) and ((.body // "") | contains($m)))] | length' 2>/dev/null || true)"
    len="$(printf '%s' "$resp" | jq -r 'if type == "array" then length else -1 end' 2>/dev/null || echo -1)"
    case "$cnt" in ''|*[!0-9]*) cnt=0 ;; esac
    case "$len" in ''|*[!0-9]*) len=-1 ;; esac
    total=$((total + cnt))
    [ "$len" -eq 100 ] || break
    page=$((page + 1))
  done
  printf '%s' "$total"
}

# Post the occurrence record. Fails loudly (non-zero) if the comment cannot be
# posted — the #2140 deaf-monitor class: a bare `curl -s` exits 0 on HTTP error.
af_post_occurrence() { # <issue-number> <what-happened>
  local n="$1" what="$2" count next resp
  count="$(af_occurrence_count "$n")"
  case "$count" in ''|*[!0-9]*) count=0 ;; esac
  next=$((count + 1))
  # `gh api` exits non-zero on an HTTP 4xx/5xx, so a failed comment fails the
  # step loudly instead of reporting a record that was never written (#2140).
  if ! resp="$(gh api "repos/${REPO}/issues/${n}/comments" --method POST \
      -f body="🔁 **Recurrence #${next}** — ${what}

This finding is already tracked by this issue, so no duplicate was filed. Occurrence recorded at \`$(date -u +%FT%TZ)\` — run: ${RUN_URL}

${AUTO_FILE_MARKER}" 2>/dev/null)"; then
    af_err "could not post the recurrence comment on issue #${n} — the occurrence is UNRECORDED; this failing run IS the alert"
    return 1
  fi
  af_actor_ok "$resp" || return 1
  af_note "recorded recurrence #${next} on issue #${n} (no duplicate filed)"
}

# The whole flow: adopt-and-comment, or file. Never both. Never neither.
af_file_or_comment() { # <stable-title> <body-file> <label>
  local title="$1" body_file="$2" label="${3:-bug}" body found resp
  [ -n "$title" ] || { af_err "--title is required (it is the dedupe key)"; return 2; }
  [ -f "$body_file" ] || { af_err "body file not found: ${body_file}"; return 2; }
  [ -n "$(af_token)" ] || {
    af_err "GH_TOKEN is not set — refusing to run a monitor that cannot file or comment (a deaf monitor is worse than no monitor)"
    return 1
  }
  af_have_gh || { af_err "gh and jq are required"; return 1; }

  found="$(af_open_issue "$title")"
  case "$found" in
    __ERR__) return 1 ;;
    *[!0-9]*) af_err "issue search returned a non-numeric issue id ('${found}') — refusing to comment on a garbage id"; return 1 ;;
  esac

  if [ -n "$found" ]; then
    af_post_occurrence "$found" "the monitor fired again (run: ${RUN_URL})" || return 1
    return 0
  fi

  body="$(cat "$body_file")"
  # `gh api` exits non-zero on an HTTP 4xx/5xx — a failed create must fail the
  # step loudly (a finding that was never filed must never look filed, #2140).
  if ! resp="$(gh api "repos/${REPO}/issues" --method POST \
      -f title="$title" -f body="$body" -f "labels[]=${label}" 2>/dev/null)"; then
    af_err "could not create the issue for '${title}' — the finding is UNFILED; this failing run IS the alert"
    return 1
  fi
  af_actor_ok "$resp" || return 1
  af_note "no open machine-authored issue for '${title}' — filed one (further occurrences will comment on it, not duplicate it)"
}

usage() {
  cat >&2 <<'USAGE'
usage: auto-file-issue.sh file --title <stable-title> --body-file <path> [--label <label>]

  Files at most ONE open issue for a monitor finding. <stable-title> is both the
  issue title and the dedupe key: no run id, timestamp or count in it. A second
  occurrence comments on the existing issue as "Recurrence #N" instead of
  filing. A failed dedupe search refuses to file (never risk a duplicate).

env: GH_TOKEN (or GITHUB_TOKEN) — MUST be the GitHub Actions token
     (secrets.GITHUB_TOKEN). The dedupe keys on author:app/github-actions, so a
     PAT writes as its own owner, matches nothing, and files a DUPLICATE on
     every run. The author is asserted from the write's own response
     (user.login); a non-Actions author fails closed.
     Also: GITHUB_REPOSITORY, GITHUB_SERVER_URL, GITHUB_RUN_ID
USAGE
  return 2
}

af_main() {
  local cmd="${1:-}" title="" body_file="" label="bug"
  shift || true
  [ "$cmd" = "file" ] || { usage; return 2; }
  while [ $# -gt 0 ]; do
    case "$1" in
      --title)     title="${2:-}"; shift 2 ;;
      --body-file) body_file="${2:-}"; shift 2 ;;
      --label)     label="${2:-}"; shift 2 ;;
      -h|--help)   usage; return 0 ;;
      *)           af_err "unknown argument: $1"; usage; return 2 ;;
    esac
  done
  af_file_or_comment "$title" "$body_file" "$label"
}

if [ "${AUTO_FILE_LIB_ONLY:-0}" != "1" ]; then
  af_main "$@"
fi
