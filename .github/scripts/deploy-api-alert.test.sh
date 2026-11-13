#!/usr/bin/env bash
# deploy-api-alert.test.sh — self-check for .github/scripts/deploy-api-alert.sh
# (#2240 — the out-of-band alert for a failed hosted-deploy job).
#
# Run: bash .github/scripts/deploy-api-alert.test.sh
# Exits 0 when ALL assertions pass, 1 on any failure. Self-contained: stubs
# `gh`/`curl` on PATH. No network, no GitHub.
#
# Coverage (#2240 acceptance in brackets):
#   1. a drift-gate failure files ONE issue whose title is the STABLE per-step
#      key — no run id, no timestamp, no count [alert on the first failing run]
#  1b. a >6000-byte report keeps its ACTIONABLE TAIL (the excerpt is the tail, not
#      the head — `head -c` would keep the noise and drop the blocking list)
#   2. that body carries the gate's OWN report (the blocking versions + the
#      ordered `gh workflow run supabase-deploy.yml --ref main` remediation),
#      so the alert cannot disagree with the run it describes
#   3. the FIRST failing step is the one attributed — a later failure in the
#      same payload does not steal the key [same step ⇒ same key]
#   4. a NON-drift failure gets its own key and NO drift section [per-step key]
#   5. a failure with NO step reported falls back to the job-level key (never a
#      crash, never a silent no-op)
#   6. an existing machine-authored issue with the exact key is COMMENTED on
#      (`Recurrence #1`), never duplicated [one issue per failing streak]
#   7. a failed dedupe search → exit 1 and NOTHING filed [never a blind dup]
#   8. no token → exit 1 BEFORE contacting GitHub [deaf-alert guard]
#   9. the drift case with an UNREADABLE report still files, and says so
#  10. the label is passed through to the create call
#  11. a Telegram delivery failure does NOT fail the step (the ledger is the
#      alert; the page is the nudge) but IS logged
#  12. Telegram is NOT called when the ledger leg failed (order: record, then
#      nudge — a paging outage must never cost the record)
#  13. the ledger leg is what fails the step when it cannot file
#  14. `--job` is required and an unknown argument is a usage error (exit 2)
#  15. the body file is written under RUNNER_TEMP and is the issue's body
#  16. a NON-Actions author is refused by the shared substrate on this path too
#      (the PAT trap: it would file a duplicate on every failing run)
#  17. the pasted report cannot break the body it is pasted into (a fence longer than
#      any backtick run in the excerpt, so repo-controlled text cannot close the block)
#  17b. the excerpt squeeze's EXIT STATUS is neither the alert's nor evidence of what it
#      did: an iconv that reports the mid-character cut in its status must not abort the
#      ledger leg, one that emits nothing must paste the ASCII FALLBACK and SAY so (never
#      claiming a squeeze, and never pasting the raw tail — the cut can leave a half
#      character in it, and an undecodable body is not a report), and the drop must be
#      disclosed from a BYTE MEASURE — so it is disclosed on a glibc that drops the
#      sequence silently (2.39, rc=0) too, and NOT disclosed when the report cannot
#      lose a byte (a measure, not a constant; case 1)
#  17c. a report with no excerpt-able text says only what is TRUE (no claim of a
#      squeeze, no empty fenced block under a heading that promises the report)
#  17d. the READ GUARD — an existing but unreadable report must not abort the alert
#  17e. a 0-byte report is READABLE and EMPTY, and must be described exactly so
#  17f. a FIFO must not HANG the alert
#  17g. main's own #12077 pin — the same two arms, converter-INDEPENDENT
#  17h. a FAILING ASCII fallback must not DEAFEN the notifier: the `|| true` guard on
#      the `tr` squeeze is a guard, not decoration — under `set -euo pipefail` its
#      absence aborts the body build before anything is filed (#2240's silence)
#  17i. the fallback's OWN empty state claims no excerpt (#12200): neither path produced
#      one, so the body must say exactly that and point at the run link — never "it is
#      pasted …" (pinned by SHAPE, not one phrasing), never the intro's "follows
#      verbatim" (the louder member of the same class), and never a GUESSED CAUSE (a
#      failing `tr` and an all-non-ASCII tail are indistinguishable here)
#  17j. a converter's own stderr diagnostic is suppressed, so the job log carries the
#      alert rather than a second, phantom failure (#12200)
#  17k. a BIG report whose TAIL is blank says the TAIL held nothing, not the FILE, and names
#      the report's real size — so a 15,400-byte report is never described as empty and the
#      6000-byte cut the intro promises is declared in that state too (#12283 F7)
#  17l. a HEALTHY converter that emits nothing is not a MISSING one: a tail that is entirely
#      undecodable makes `iconv -c` emit zero bytes with a status glibc uses to say it dropped
#      everything, and the state sentence names that cause instead of blaming the host
#  17m. the ASCII fallback's own stderr diagnostic is suppressed too — `tr` shares the
#      notifier's job, so its complaint must not read as a second failure
#  18. a LATE `deploy-api` failure must not read as "nothing shipped"
#  19-21. `--resolve` (#8841) retires EXACTLY the keys whose steps reported
#      `success` in this run (plus the job-level key only when `job.status` is
#      `success`) — `skipped` and outcome-less steps are never read as a pass, and
#      a failing step's streak is never retired
#  22. a failed close is a WARNING and exit 0 — this leg runs on the GREEN path, so
#      it must never become a false deploy failure
#  23. an untrusted search closes NOTHING (fail closed) and still exits 0
#  24. a missing token warns (never reds the job) and contacts nothing
#  25. the ALERT path never closes anything (a failing run has not recovered)
#  26. `--job-status` without `--resolve` (and an unknown status) is a usage error

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SCRIPT_DIR/deploy-api-alert.sh"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  ❌ $1"; }
assert_eq() { if [ "$1" = "$2" ]; then ok "$3"; else bad "$3 (got '$1', want '$2')"; fi; }
assert_contains() {
  case "$1" in *"$2"*) ok "$3" ;; *) bad "$3 (missing '$2')" ;; esac
}
assert_not_contains() {
  case "$1" in *"$2"*) bad "$3 (unexpectedly found '$2')" ;; *) ok "$3" ;; esac
}
assert_match() { # <haystack> <ERE> <label>
  if grep -qE -- "$2" <<<"$1"; then ok "$3"; else bad "$3 (no line matches: $2)"; fi
}
assert_no_match() { # <haystack> <ERE> <label>
  if grep -qE -- "$2" <<<"$1"; then bad "$3 (a line matches: $2)"; else ok "$3"; fi
}

FIX="$(mktemp -d)"
trap 'rm -rf "$FIX"' EXIT
BIN="$FIX/bin"
mkdir -p "$BIN"
export STUB_TMP="$FIX/stub"
mkdir -p "$STUB_TMP"

# ── stub: gh (exactly the shapes the substrate uses) ───────────────────────
cat > "$BIN/gh" <<'GH_EOF'
#!/usr/bin/env bash
[ "${1:-}" = "api" ] || { echo "GH unexpected: $*" >&2; exit 1; }
path="${2:-}"; method="GET"; paginate=0
shift 2 || true
fields=()
while [ $# -gt 0 ]; do
  case "$1" in
    --method) method="$2"; shift 2 ;;
    --jq) shift 2 ;;
    --paginate) paginate=1; shift ;;
    -f) fields+=("$2"); shift 2 ;;
    *) shift ;;
  esac
done
echo "GH ${method} ${path}" >> "$STUB_TMP/calls.log"
# NB: the default lives in a variable — a literal JSON object inside
# `${VAR:-{...}}` is mis-parsed (the first '}' closes the expansion), which is
# how this stub first shipped and made every create case look like a refusal.
DEFAULT_ITEMS_JSON='{"items":[]}'
case "$path" in
  user)
    # The real installation-token 403 (actions/runner#3289). Nothing may need
    # this call — the substrate asserts the author from the WRITE's response.
    echo 'gh: Resource not accessible by integration (HTTP 403)' >&2
    exit 1 ;;
  search/issues*)
    [ "${STUB_SEARCH_FAIL:-0}" = "1" ] && { echo "gh: search failed" >&2; exit 1; }
    if [ "${STUB_SEARCH_DYNAMIC:-0}" = "1" ]; then
      # #8841: the resolve leg searches ONE KEY PER PASSING STEP, so a fixed
      # fixture cannot tell the keys apart. Derive the item from the QUERY's own
      # quoted title (url-decoded) and record it, so a case can assert WHICH key
      # was searched — and the substrate's EXACT-title guard stays honest.
      python3 - "$path" "$STUB_TMP" <<'PYEOF'
import json, re, sys, urllib.parse
q = urllib.parse.unquote(sys.argv[1])
quoted = re.findall(r'"([^"]*)"', q)
title = quoted[-1] if quoted else ""
with open(sys.argv[2] + "/searched.txt", "a", encoding="utf-8") as fh:
    fh.write(title + "\n")
json.dump({"items": [{"number": 42, "title": title,
                       "user": {"login": "github-actions[bot]", "type": "Bot"}}]},
          sys.stdout)
PYEOF
      exit 0
    fi
    printf '%s\n' "${STUB_SEARCH_JSON:-$DEFAULT_ITEMS_JSON}"
    if [ "$paginate" = "1" ] && [ -n "${STUB_SEARCH_JSON_P2:-}" ]; then printf '%s\n' "$STUB_SEARCH_JSON_P2"; fi
    exit 0 ;;
  */comments\?*)
    [ "${STUB_COMMENTS_FAIL:-0}" = "1" ] && { echo "gh: comments failed" >&2; exit 1; }
    printf '%s' "${STUB_COMMENTS_JSON:-[]}" ;;
  */comments)
    [ "${STUB_COMMENT_FAIL:-0}" = "1" ] && { echo "gh: comment failed" >&2; exit 1; }
    printf '%s\n' "${fields[@]}" > "$STUB_TMP/comment.txt"
    printf '{"user":{"login":"%s"}}' "${STUB_COMMENT_LOGIN:-github-actions[bot]}" ;;
  */issues)
    [ "${STUB_CREATE_FAIL:-0}" = "1" ] && { echo "gh: create failed" >&2; exit 1; }
    printf '%s\n' "${fields[@]}" > "$STUB_TMP/created.txt"
    printf '{"number":900,"user":{"login":"%s"}}' "${STUB_CREATE_LOGIN:-github-actions[bot]}" ;;
  */issues/[0-9]*)
    # PATCH close — the resolve leg (#8841). The closed TITLE is recorded so a
    # case can assert WHICH key was retired (the resolve leg must retire exactly
    # the steps that passed).
    [ "${STUB_CLOSE_FAIL:-0}" = "1" ] && { echo "gh: patch failed" >&2; exit 1; }
    printf 'closed #%s\n' "${path##*/}" >> "$STUB_TMP/closed.txt"
    printf '{"number":%s,"state":"closed","user":{"login":"github-actions[bot]"}}' "${path##*/}" ;;
  *) printf '{}' ;;
esac
GH_EOF
chmod +x "$BIN/gh"

# ── stub: curl (Telegram leg) ───────────────────────────────────────────────
cat > "$BIN/curl" <<'CURL_EOF'
#!/usr/bin/env bash
echo "CURL $*" >> "$STUB_TMP/calls.log"
out=""
args=("$@")
for i in "${!args[@]}"; do
  case "${args[$i]}" in -o) out="${args[$((i + 1))]}" ;; esac
done
if [ "${STUB_TG_FAIL:-0}" = "1" ]; then
  echo "curl: (7) Failed to connect" >&2
  exit 7
fi
[ -n "$out" ] && printf '%s' '{"ok":true}' > "$out"
exit 0
CURL_EOF
chmod +x "$BIN/curl"
PATH="$BIN:$PATH"
export PATH

# ── ambient: exactly what Actions provides, plus what the job must bind ────
export GITHUB_REPOSITORY="daniel-ospina/tortoise"
export GITHUB_SERVER_URL="https://github.com"
export GITHUB_RUN_ID="987654321"
export GITHUB_SHA="deadbeefcafe"
export GITHUB_WORKFLOW_REF="daniel-ospina/tortoise/.github/workflows/deploy-hosted.yml@refs/heads/main"
export RUNNER_TEMP="$FIX/runner-temp"
mkdir -p "$RUNNER_TEMP"
DRIFT_REPORT="$FIX/drift-report.txt"
cat > "$DRIFT_REPORT" <<'REPORT_EOF'
check-migration-drift: repo migrations vs prod (project ybetwichurajbfswfeqa)
  BLOCKING repo-ahead migrations (repo has them; prod's supabase_migrations.schema_migrations has NO row):
    - 20260926000002
    - 20260930000001
  note: BLOCKING list above is the COMPLETE repo-ahead set — 2 of 2 repo-ahead pending
  OUT OF ORDER (sort before prod's newest applied version 20261001000001) — do NOT apply these as-is:
    - 20260926000002
  Remediation (in this order):
    1. resolve each OUT OF ORDER version above (a or b);
    2. then dispatch the deploy for the pending set above —
       gh workflow run supabase-deploy.yml --ref main
REPORT_EOF

reset_case() {
  : > "$STUB_TMP/calls.log"
  rm -f "$STUB_TMP/created.txt" "$STUB_TMP/comment.txt" "$STUB_TMP/closed.txt" \
        "$STUB_TMP/searched.txt"
  # ⛔ #12077: the BODY file is a reused artifact and must not survive a case. A case
  # that aborts BEFORE `build_body` opens the file (case 8's missing token is one)
  # leaves the PREVIOUS case's body on disk, and any later assertion that reads it
  # then asserts about the wrong case. (Corrected measurement, review round 2: the
  # mid-character case's own green was NOT this — `build_body` truncates the file in
  # place, so that assertion passed on a TRUNCATED ASCII partial body, which is valid
  # UTF-8 while carrying no report. Both are fixed by the two changes below and the
  # assertion that the body carries the report.)
  rm -f "$RUNNER_TEMP/deploy-api-alert-body.md"
  unset STUB_SEARCH_JSON STUB_SEARCH_JSON_P2 STUB_SEARCH_FAIL STUB_CREATE_FAIL \
        STUB_COMMENT_FAIL STUB_COMMENTS_FAIL STUB_COMMENTS_JSON \
        STUB_CREATE_LOGIN STUB_COMMENT_LOGIN STUB_TG_FAIL STUB_CLOSE_FAIL \
        STUB_SEARCH_DYNAMIC || true
  export GH_TOKEN="test-token"
  export TELEGRAM_BOT_TOKEN="tg-secret"
  export TELEGRAM_CHAT_ID="12345"
}

run_alert() { # <args…> -> RC, OUT
  local so
  so="$("$HELPER" "$@" 2>&1)"
  RC=$?
  OUT="$so"
}
count_calls()  { grep -c -- "$1" "$STUB_TMP/calls.log" 2>/dev/null || true; }
# Bound to the END of the path: a bare `…/issues` substring also matches
# `…/issues/42/comments`, so a naive count read a recurrence comment as a filed
# duplicate (and would have hidden a real duplicate behind the same collapse).
count_creates()  { grep -cE -- 'GH POST .*/issues$' "$STUB_TMP/calls.log" 2>/dev/null || true; }
count_comments() { grep -cE -- 'GH POST .*/issues/[0-9]+/comments$' "$STUB_TMP/calls.log" 2>/dev/null || true; }
count_closes()   { grep -cE -- 'GH PATCH .*/issues/[0-9]+$' "$STUB_TMP/calls.log" 2>/dev/null || true; }
title_line()   { grep -m1 '^title=' "$STUB_TMP/created.txt" 2>/dev/null || echo ''; }
created()      { [ -f "$STUB_TMP/created.txt" ] && cat "$STUB_TMP/created.txt" || echo ''; }
commented()    { [ -f "$STUB_TMP/comment.txt" ] && cat "$STUB_TMP/comment.txt" || echo ''; }
search_json() { # <number> [title] [login]
  printf '{"items":[{"number":%s,"title":"%s","user":{"login":"%s","type":"Bot"}}]}' \
    "${1:-42}" "${2:-}" "${3:-github-actions[bot]}"
}

DRIFT_STEPS="parity=success verify-secrets=success provenance=success drift=failure fly-machines=skipped set-secrets=skipped deploy=skipped"

echo "── case 1/2/10/15: drift gate fails → ONE issue with the gate's report ─"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "0" "the alert leg succeeded"
assert_eq "$(count_calls 'GH POST repos/daniel-ospina/tortoise/issues')" "1" "exactly one issue filed"
CREATED="$(created)"
assert_contains "$(title_line)" "title=deploy-hosted: deploy-api failed at 'drift'" "the title is the stable per-step key (step id 'drift')"
assert_not_contains "$(title_line)" "987654321" "the TITLE carries NO run id (that is the #2706 duplicate-spam defect; the id belongs in the body)"
assert_contains "$CREATED" "labels[]=bug" "the label is passed through"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "BLOCKING repo-ahead migrations" "the body carries the gate's own report"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "gh workflow run supabase-deploy.yml --ref main" "the body carries the exact remediation"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "Check migration drift (fail-closed)" "the body names the failed step"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "987654321" "the body links the run (the detail lives there)"
# ⛔ The disclosure is a MEASURE, not a CONSTANT (review P2, PR #12110). Every other
# arm asserts the disclosure PRESENT; none asserted it ABSENT when nothing was
# dropped, so an implementation that discloses whenever the squeeze path RAN — rather
# than when the bytes actually differ — was green on all of them (measured by the
# reviewer: 159/159, 0 red, on both hosts). It then publishes "characters … were
# dropped" about a report it carried verbatim, which is this PR's own declared threat
# class: a disclosure that states a cause/magnitude it did not measure.
# This fixture is under the 6000-byte cap and decodes as UTF-8 on its own, so
# `iconv -c` can drop NOTHING from it (it drops invalid sequences, not valid multibyte
# characters — the em dashes in the gate's own report survive). The precondition is
# asserted first, or the row below is silently vacuous.
if [ "$(wc -c < "$DRIFT_REPORT")" -lt 6000 ] && python3 -c 'import sys; open(sys.argv[1], "rb").read().decode("utf-8")' "$DRIFT_REPORT"; then
  ok "case 1's fixture is under the 6000-byte cap and decodes as UTF-8, so no byte can be dropped here"
else
  bad "case 1's fixture holds an invalid UTF-8 sequence or exceeds the cap — the no-drop pin below would be vacuous"
fi
assert_not_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "NOT byte-identical" "and the body does NOT claim a drop it did not measure (a measure, not a constant)"

echo "── case 1b: a >6000-byte report keeps its ACTIONABLE TAIL ─────────────"
# The gate prints its context (remote-ahead, non-conforming files, the
# warn-class preamble) BEFORE the actionable block and ENDS with the BLOCKING
# list and the ordered remediation. So the excerpt must be the TAIL of the
# report: `head -c 6000` keeps the noise and drops the one thing the alert
# exists to carry. This fixture is larger than the cap with the parts on
# opposite sides of it, so the direction is pinned and not merely asserted.
BIG_REPORT="$FIX/drift-report-big.txt"
{
  echo "check-migration-drift: repo migrations vs prod (project ybetwichurajbfswfeqa)"
  echo "  NOISE-BEFORE-THE-CUT-MARKER"
  i=0
  while [ "$i" -lt 400 ]; do
    echo "  warn-class line $i — index-only drift, non-blocking, listed for context only"
    i=$((i + 1))
  done
  echo "  BLOCKING repo-ahead migrations (repo has them; prod's supabase_migrations.schema_migrations has NO row):"
  echo "    - 20260926000002"
  echo "  Remediation — decide PER VERSION, from prod:"
  echo "    (a) the objects are ABSENT — apply migrations first:"
  echo "    gh workflow run supabase-deploy.yml --ref main"
} > "$BIG_REPORT"
if [ "$(wc -c < "$BIG_REPORT")" -gt 6000 ]; then
  ok "the fixture exceeds the 6000-byte cap ($(wc -c < "$BIG_REPORT") bytes) — otherwise this case is vacuous"
else
  bad "the fixture must exceed the cap, or head and tail are indistinguishable"
fi
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$BIG_REPORT"
assert_eq "$RC" "0" "the alert still succeeds on a large report"
BIG_BODY="$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")"
assert_contains "$BIG_BODY" "BLOCKING repo-ahead migrations" "the ACTIONABLE block survives the cap (tail, not head)"
assert_contains "$BIG_BODY" "gh workflow run supabase-deploy.yml --ref main" "and the ordered remediation survives — head -c drops both"
assert_not_contains "$BIG_BODY" "NOISE-BEFORE-THE-CUT-MARKER" "the preamble is what gets truncated away"
assert_contains "$BIG_BODY" "LAST 6000 bytes" "and the truncation is stated, so a reader knows why the head is missing"

echo "── case 3: the FIRST failing step is attributed ─────────────────────"
reset_case
run_alert --job deploy-api \
  --steps "parity=success verify-secrets=failure drift=skipped deploy=failure" \
  --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "0" "the alert leg succeeded"
assert_contains "$(title_line)" "title=deploy-hosted: deploy-api failed at 'verify-secrets'" "the FIRST failing step owns the key"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "Verify secrets exist" "the body names that step"
assert_not_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "BLOCKING repo-ahead migrations" "no drift report on a non-drift failure"

echo "── case 4: a non-drift step gets its OWN key ───────────────────────"
reset_case
run_alert --job deploy-api --steps "parity=success deploy=failure" --drift-report "$DRIFT_REPORT"
assert_contains "$(title_line)" "title=deploy-hosted: deploy-api failed at 'deploy'" "a different step ⇒ a different key (a different finding)"
assert_not_contains "$(title_line)" "drift" "the drift key is not reused"

echo "── case 5: no step reported → job-level key, never a crash ─────────"
reset_case
run_alert --job deploy-api --steps "" --drift-report ""
assert_eq "$RC" "0" "an unattributed failure still alerts"
assert_contains "$(title_line)" "title=deploy-hosted: deploy-api failed" "the job-level fallback key is used"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "not reported" "the body says the step was not reported"

echo "── case 6: an existing machine-authored issue → Recurrence, no dup ──"
reset_case
export STUB_SEARCH_JSON="$(search_json 42 "deploy-hosted: deploy-api failed at 'drift'")"
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "0" "the recurrence leg succeeded"
assert_eq "$(count_creates)" "0" "no duplicate issue was filed"
assert_eq "$(count_comments)" "1" "the occurrence was recorded on the existing issue"
assert_contains "$(commented)" "Recurrence #1" "the occurrence count is visible (#3907)"

echo "── case 16: a NON-Actions author is refused (the PAT trap) ─────────"
reset_case
export STUB_CREATE_LOGIN="some-human"
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "1" "a non-Actions author fails the step loudly"
assert_contains "$OUT" "REQUIRES the GitHub Actions token" "the misuse is named (it would duplicate every run)"

echo "── case 7: a failed dedupe search → refuse, file nothing ───────────"
reset_case
export STUB_SEARCH_FAIL=1
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "1" "a failed search fails the step"
assert_eq "$(count_creates)" "0" "nothing was filed (never a blind duplicate)"
assert_eq "$(count_calls 'CURL')" "0" "and no page was sent (the record comes first)"

echo "── case 8: no token → refuse BEFORE contacting GitHub ──────────────"
reset_case
unset GH_TOKEN GITHUB_TOKEN || true
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "1" "no token fails the step"
assert_eq "$(count_calls 'GH')" "0" "no GitHub call at all"
assert_contains "$OUT" "GH_TOKEN is not set" "the refusal is loud"
export GH_TOKEN="test-token"

echo "── case 9: an unreadable drift report still alerts, and says so ────"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/does-not-exist.txt"
assert_eq "$RC" "0" "the alert still files"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "report file was not readable" "the degradation is named in the body"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "does NOT re-run the gate" "and it explains why it does not re-read prod"

echo "── case 11/12/13: the two legs, and their order ────────────────────"
reset_case
export STUB_TG_FAIL=1
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "0" "a Telegram failure does NOT fail the step (the ledger is the alert)"
assert_contains "$OUT" "telegram page NOT delivered" "the paging failure is still surfaced"
assert_eq "$(count_calls 'CURL')" "1" "the page was attempted"
reset_case
export STUB_CREATE_FAIL=1
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "1" "a ledger failure fails the step (a finding must never look filed)"
assert_eq "$(count_calls 'CURL')" "0" "and the nudge is skipped — the record comes first"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "0" "the happy path succeeds"
assert_eq "$(count_calls 'CURL')" "1" "the page is sent when the ledger recorded the finding"

echo "── case 17: the other two sites get their OWN key and their own meaning ──"
# `packaging-smoke` and `post-deploy-verify` can each fail while `deploy-api` is
# SKIPPED, so a notifier only inside `deploy-api` would leave that silent. They
# are not interchangeable with it either: a red in one means the deploy was
# SKIPPED, in the other that a bad release is already live.
reset_case
run_alert --job packaging-smoke --steps "buildx=success build-image=failure pack-assert=skipped"
assert_eq "$RC" "0" "a failed pack smoke alerts"
assert_contains "$(title_line)" "title=deploy-hosted: packaging-smoke failed at 'build-image'" "the pack-smoke key names ITS job and step"
assert_not_contains "$(title_line)" "deploy-api" "and cannot be mistaken for a deploy-api failure"
PACK_BODY="$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")"
assert_contains "$PACK_BODY" "Build hosted image" "the body names the failed step"
assert_contains "$PACK_BODY" "SKIPPED" "the body says the deploy was skipped (what a red HERE means)"
assert_not_contains "$PACK_BODY" "LIVE and unhealthy" "and not another job's meaning"
reset_case
run_alert --job post-deploy-verify --steps "health-gate=failure bypass-report=skipped machine-env=skipped"
assert_eq "$RC" "0" "a failed post-deploy verification alerts"
assert_contains "$(title_line)" "title=deploy-hosted: post-deploy-verify failed at 'health-gate'" "its own key"
POST_BODY="$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")"
assert_contains "$POST_BODY" "LIVE and unhealthy" "the body says a bad release is live, NOT that the deploy failed"
assert_not_contains "$POST_BODY" "verified end-to-end" "and not the deploy-api meaning (the deploy did succeed here)"

# ── case 18: a LATE `deploy-api` failure must not read as "nothing shipped" ──
# `Deploy` runs BEFORE the `if: always()` gate audit, and the alert is the job's
# LAST step — so a red here can happen with a release already created. The body's
# consequence must defer to the failed step rather than assert the pre-deploy
# meaning. (Round-6 review, P3.)
reset_case
run_alert --job deploy-api --steps "deploy=success bypass-report=failure"
assert_eq "$RC" "0" "a late deploy-api failure still alerts"
DEPLOY_LATE_BODY="$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")"
assert_contains "$DEPLOY_LATE_BODY" "verified end-to-end" "the body states the pipeline invariant, true for every step"
assert_contains "$DEPLOY_LATE_BODY" "failed step" "and defers 'did anything ship?' to the failed-step row"
assert_not_contains "$DEPLOY_LATE_BODY" "nothing has shipped" "it must NOT assert the pre-Deploy meaning unconditionally"
assert_not_contains "$DEPLOY_LATE_BODY" "did **NOT** flip" "nor the old unconditional flip claim"
# The opener used to hard-code "the hosted deploy pipeline is blocked" for all
# three jobs — FALSE here, where the deploy already succeeded and the release is
# live. The job's own meaning must be the only claim in the body. (Round-4
# review, P2-1: a body that misleads the on-call reader is the defect this whole
# change exists to remove.)
assert_not_contains "$POST_BODY" "blocked" "the body makes no 'pipeline is blocked' claim for a job that does not block it"
assert_contains "$PACK_BODY" "SKIPPED" "(control) the pack-smoke body DOES state the consequence that is true there"

# ── case 17: the pasted report cannot break the body it is pasted into ───────
# The report is pasted into a fenced block. Two ways it could damage the issue:
# a fence run inside the excerpt closing the block early (the rest then renders as
# live markdown), and `tail -c` landing inside a multibyte character (invalid
# UTF-8 in a body GitHub then mangles). Both are pinned here. (Round-5, P3-2.)
FENCE_REPORT="$FIX/drift-report-fence.txt"
{
  echo "check-migration-drift: repo migrations vs prod"
  # Exact shipped heading (double-quoted: the heading carries an apostrophe and no
  # fence, so nothing forces the single-quoted form). Round-9 review: the earlier
  # single-quoted variant silently diverged from what the gate emits, and nothing
  # in this case asserts on the heading, so the drift was undetectable.
  echo "  BLOCKING repo-ahead migrations (repo has them; prod's supabase_migrations.schema_migrations has NO row):"
  echo "    - 20260926000002   # see ###\`\`\` the runbook"
  echo '  ```'
  echo '  Remediation — decide PER VERSION, from prod:'
  echo '    (a) the objects are ABSENT — apply migrations first:'
  echo '    gh workflow run supabase-deploy.yml --ref main'
} > "$FENCE_REPORT"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FENCE_REPORT"
assert_eq "$RC" "0" "the alert succeeds on a report containing a fence"
FENCE_BODY="$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")"
assert_contains "$FENCE_BODY" "gh workflow run supabase-deploy.yml --ref main" "the remediation is still carried"
assert_contains "$FENCE_BODY" '````' "the block is fenced with MORE backticks than the excerpt contains, so it cannot close early"
# Byte-boundary: cut the report mid-character and require the body to stay valid
# UTF-8 (`iconv -c` drops the split sequence). The fixture is built so the 6000-
# byte tail cut lands EXACTLY inside a two-byte `é` — and that property is itself
# asserted, so this case cannot silently become vacuous.
python3 - "$FIX/drift-report-utf8.txt" <<'PY'
import sys, pathlib
head = b"check-migration-drift\n" + b"x" * 5900
payload = b"\n  BLOCKING repo-ahead migrations\n    - 20260926000002\n    gh workflow run supabase-deploy.yml --ref main\n"
# Derive the padding so the cut (`total - 6000`) lands on the SECOND byte of the
# 2-byte `é`: total = len(head) + 2 + len(payload) + pad + 1  and
# total - 6000 == len(head) + 1.
pad = (len(head) + 1 + 6000) - (len(head) + 2 + len(payload) + 1)
assert pad > 0, pad
pathlib.Path(sys.argv[1]).write_bytes(head + "é".encode() + payload + b"#" * pad + b"\n")
PY
if tail -c 6000 "$FIX/drift-report-utf8.txt" | python3 -c "import sys; sys.stdin.buffer.read().decode('utf-8')" 2>/dev/null; then
  bad "the utf8 fixture must be cut MID-CHARACTER, or this case is vacuous"
else
  ok "the fixture really is cut inside a multibyte character (the case is not vacuous)"
fi
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/drift-report-utf8.txt"
assert_eq "$RC" "0" "the alert succeeds on a report cut mid-character"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "BLOCKING repo-ahead migrations" "and the body it wrote carries the report, not just a valid-but-partial file"
if python3 -c "import sys,pathlib; pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')" "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null; then
  ok "the body is valid UTF-8 even though the cut landed inside a multibyte character"
else
  bad "the body is NOT valid UTF-8 — a split multibyte character reached the issue"
fi
# The RESET invariant, pinned directly. A body left behind by an earlier case is
# indistinguishable from this case's when an abort happens before `build_body` opens
# the file, so `reset_case` must remove it — otherwise a later assertion can read a
# stale artifact.
reset_case
if [ -e "$RUNNER_TEMP/deploy-api-alert-body.md" ]; then
  bad "reset_case leaves the previous case's body file in place — an assertion could read it as its own"
else
  ok "reset_case clears the reused body file, so no case can assert on a stale body"
fi

# ── case 17b: the SQUEEZE's exit status is not the alert's (#12077) ──────────
# `iconv -c` reports the mid-character cut in its EXIT STATUS on glibc 2.43
# (`ubuntu-26.04`, the runner image: the convertible bytes are emitted and rc is
# still 1) while glibc 2.39 exits 0 — so on an ordinary box the defect is
# INVISIBLE and an ordinary box cannot see it. THREE arms are simulated here so the
# harness discriminates on any host: (a) status 1 with the bytes emitted must still
# file the alert, (b) an iconv that emits NOTHING must not paste an empty block under
# a heading that promises the report verbatim (it must say it went to the ASCII
# fallback), and (c) the SILENT shape — bytes emitted, the split character dropped,
# status 0 — must still DISCLOSE the drop.
#
# ⚠️ SCOPE OF THIS CASE, stated because it is not obvious: the status-catch half of
# #12077 was landed INDEPENDENTLY on `main` (#12020, `49cf5193541f`) before this
# branch's merge base, so arm (a) below is GREEN on the pre-fix code this PR merges
# into — it is the host-real-`iconv` duplicate of case 17g (which pins the same two
# properties with a stub, converter-independently), NOT the pin that carries this
# PR's Root 2. What is new at this head is the BYTE-MEASURED disclosure (arms b/c),
# the guarded re-read, the `-e`/`! -p` widening and the ASCII-fallback wording.
PATH_BEFORE_ICONV_SHIM="$PATH"
REAL_ICONV="$(command -v iconv)"
# The arm below simulates glibc 2.43's `iconv -c` by wrapping the REAL one. With no
# real iconv on the host the wrapper degrades to `"" "$@"` — which emits nothing
# and exits 1, i.e. it silently becomes arm (b), and arm (a) would pass while
# squeezing nothing. Fail loudly instead of asserting coverage we do not have.
if [ -n "$REAL_ICONV" ]; then
  ok "case 17b: a real iconv exists, so the status shim can wrap it"
else
  bad "case 17b: no iconv on PATH — arm (a) would degenerate into the raw-fallback arm and prove nothing"
fi
ICONV_STATUS_DIR="$FIX/bin-iconv-status"
mkdir -p "$ICONV_STATUS_DIR"
cat > "$ICONV_STATUS_DIR/iconv" <<ICONV_STATUS_EOF
#!/usr/bin/env bash
"$REAL_ICONV" "\$@"
exit 1
ICONV_STATUS_EOF
chmod +x "$ICONV_STATUS_DIR/iconv"
export PATH="$ICONV_STATUS_DIR:$PATH"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/drift-report-utf8.txt"
assert_eq "$RC" "0" "an iconv that reports the cut in its STATUS does not fail the alert (host REAL iconv — a duplicate of case 17g, already true at the merge base)"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "BLOCKING repo-ahead migrations" "and the squeezed excerpt is still carried"
# ⚠️ The two assertions above are ALSO satisfied by the raw-tail fallback (its tail
# carries the same `BLOCKING` line), so on their own they cannot tell the squeeze
# from the fallback — a "fix" that discards iconv's bytes whenever it exits
# non-zero would pass them while pasting the invalid-UTF-8 raw tail, re-creating
# the artifact case 17 forbids. These make the arm name the SQUEEZE path.
assert_not_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "could not be byte-squeezed" "and it is the SQUEEZED excerpt, not the raw fallback"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "NOT byte-identical" "and the characters the squeeze dropped are disclosed beside the excerpt"
if python3 -c "import sys,pathlib; pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')" "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null; then
  ok "and the body stayed valid UTF-8 on the squeeze path (the raw tail's is not)"
else
  bad "the squeezed body is NOT valid UTF-8 — the raw fallback's bytes reached the issue"
fi

# ── arm (c): the SILENT drop — bytes emitted, split character gone, status 0 ─────────
# glibc 2.39 (this box, and every ordinary dev host) drops the split sequence and exits
# 0, so the drop is visible ONLY from the bytes. The mirror of arm (a): the REAL
# converter, then `exit 0` regardless of what it reported — i.e. glibc 2.39's shape
# reproduced on a 2.43 host and vice versa. An implementation that derives the
# disclosure from `$?` says "nothing was dropped" here and reds the assertion below
# ON THE RUNNER TOO; on arm (a) alone it passes on every host. This is what makes
# "measure, not status" a pinned property rather than a host generation's accident.
ICONV_SILENT_DIR="$FIX/bin-iconv-silent"
mkdir -p "$ICONV_SILENT_DIR"
cat > "$ICONV_SILENT_DIR/iconv" <<ICONV_SILENT_EOF
#!/usr/bin/env bash
"$REAL_ICONV" "\$@"
exit 0
ICONV_SILENT_EOF
chmod +x "$ICONV_SILENT_DIR/iconv"
export PATH="$ICONV_SILENT_DIR:$PATH_BEFORE_ICONV_SHIM"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/drift-report-utf8.txt"
assert_eq "$RC" "0" "an iconv that drops the split character and reports SUCCESS does not fail the alert either"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "BLOCKING repo-ahead migrations" "arm c: the squeezed excerpt is carried"
assert_not_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "could not be byte-squeezed" "arm c: and it is the SQUEEZED excerpt, not the ASCII fallback"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "NOT byte-identical" "arm c: and the SILENTLY dropped character is still disclosed — the measure, not the status (this reds a status-based implementation on every host)"

ICONV_BROKEN_DIR="$FIX/bin-iconv-broken"
mkdir -p "$ICONV_BROKEN_DIR"
printf '#!/usr/bin/env bash\nexit 1\n' > "$ICONV_BROKEN_DIR/iconv"
chmod +x "$ICONV_BROKEN_DIR/iconv"
export PATH="$ICONV_BROKEN_DIR:$PATH_BEFORE_ICONV_SHIM"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/drift-report-utf8.txt"
assert_eq "$RC" "0" "an iconv that emits nothing still files the alert"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "could not be byte-squeezed" "and says the excerpt is an ASCII fallback, never a verbatim report it does not carry"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "non-ASCII" "and DECLARES the non-ASCII loss, so a mangled report never reads as the gate's own text"
assert_not_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "NOT byte-identical" "and it does NOT claim a squeeze it did not perform"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "BLOCKING repo-ahead migrations" "and the ASCII report is still carried, so the operator sees what is blocking"
# ⛔ R4's CARRYING half (review round 2, P2). 17i pins the empty state hard; nothing pinned the
# direction that matters just as much: in the state where the fallback DOES produce text, the body
# must say it is pasted AND actually paste it — and must never also claim nothing was. Without these
# three rows an edit that emits the empty-state sentence from the carrying arm passes every other
# assertion in this file (183/183, measured), shipping "it is pasted with its non-ASCII bytes
# dropped … NOTHING is pasted below" directly above a fenced report: the on-call reader concludes
# the report is absent and leaves for the run, which is the exact consumer-side failure #12200 is
# about, mirrored.
FALLBACK_BODY="$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)"
assert_contains "$FALLBACK_BODY" "it is pasted with its non-ASCII bytes dropped" "and the CARRYING arm claims the paste it really performs"
assert_not_contains "$FALLBACK_BODY" "NOTHING is pasted below" "and it never ALSO claims nothing was pasted — the two states' sentences are exclusive"
assert_contains "$FALLBACK_BODY" '```' "and the excerpt is FENCED, so the claim is accompanied by the artifact it describes"
# This arm is reached only when NO converter ran, so the split character is still in
# the raw bytes: the fallback must drop it (not paste it), or the issue body is
# undecodable. Pinned here as well as in case 17g, because a "fix" that swaps this
# branch back to `excerpt="$raw_tail"` reds THIS row on any host.
if python3 -c "import sys,pathlib; pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')" "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null; then
  ok "and the fallback body is valid UTF-8 — the non-ASCII cut is dropped, never pasted"
else
  bad "the fallback body is NOT valid UTF-8 — the raw tail reached the issue"
fi
export PATH="$PATH_BEFORE_ICONV_SHIM"

# ── case 17h: a failing ASCII FALLBACK must not deafen the notifier ──────────────────
# `tr` is coreutils, but the guard around it is what a merge already dropped once
# (review P2, PR #12110): without `|| true`, `set -e` aborts `build_body` AT the
# assignment and NOTHING is filed — the exact silence #2240 exists to remove. The
# guard is pinned here rather than assumed away as "tr never fails".
REAL_TR="$(command -v tr)"
if [ -n "$REAL_TR" ]; then
  ok "case 17h: a real tr exists, so the failing-squeeze shim can wrap it"
else
  bad "case 17h: no tr on PATH — this arm cannot reproduce the failing fallback"
fi
TR_BROKEN_DIR="$FIX/bin-tr-broken"
mkdir -p "$TR_BROKEN_DIR"
cat > "$TR_BROKEN_DIR/tr" <<TR_BROKEN_EOF
#!/usr/bin/env bash
# Fail ONLY the ASCII-fallback squeeze: \`-d\` is that call's own flag, so
# auto-file-issue.sh's \`tr '[:upper:]' '[:lower:]'\` still runs and this arm cannot red
# for the WRONG reason (a refused author instead of a dead alert).
for a in "\$@"; do [ "\$a" = "-d" ] && exit 1; done
exec "$REAL_TR" "\$@"
TR_BROKEN_EOF
chmod +x "$TR_BROKEN_DIR/tr"
export PATH="$ICONV_BROKEN_DIR:$TR_BROKEN_DIR:$PATH_BEFORE_ICONV_SHIM"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/drift-report-utf8.txt"
export PATH="$PATH_BEFORE_ICONV_SHIM"
assert_eq "$RC" "0" "case 17h: a failing ASCII fallback does NOT fail the alert (the ledger is the alert, the notifier is the safety net)"
assert_eq "$(count_creates)" "1" "case 17h: and an issue IS still filed — a degraded body, never a deaf channel"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "could not be byte-squeezed" "case 17h: and the degradation is STATED rather than silent"
# ⛔ The SAME empty state is reachable from here (a broken `tr` produces no excerpt too), and
# this arm is why the sentence may not name a CAUSE: an earlier wording asserted "every byte
# of the excerpt-able tail is non-ASCII", which is false for THIS state — an ASCII report
# whose fallback command failed (measured, VGATE round 1). The state is named, the cause is
# not guessed; a cause-specific sentence reds this row.
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "ASCII fallback produced no excerpt either" "case 17h: and a FAILING fallback is described as itself, not as a non-ASCII report"
assert_not_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "is non-ASCII" "case 17h: and it does NOT diagnose the report's bytes — a failing tr and an all-non-ASCII tail are indistinguishable from here"
# …and this report is 11,923 bytes, so THIS arm must declare the 6000-byte cut as well
# (review round 6, P3): the intro promises it beside the excerpt in every state.
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "only its last 6000 were read" "case 17h: and the 6000-byte cut is DECLARED in this arm too, not only in the empty-capture arm"

# ── case 17i: the ASCII fallback's OWN empty state claims no excerpt (#12200) ─────────
# The fallback branch has TWO outcomes and the sentence that described them was
# unconditional: a report whose excerpt-able text is entirely non-ASCII (`→→→`)
# squeezes to NOTHING under `tr -d '\200-\377'`, so the fenced block is skipped — while
# the body still said "it is pasted with its non-ASCII bytes dropped". That is a claim
# about evidence made on a report the issue does not carry: with a degraded excerpt the
# reader reads the body; with no excerpt at all they must follow the run link. Same class
# as case 17c, on the path where the promise is prose instead of a heading — which is the
# class this whole file is about ("write the sentence about the ARTIFACT, not about the
# code path that produced it").
#
# The same class had a LOUDER member: the section intro said the report "follows
# verbatim" for every drift body, before the report is even read. It is pinned absent here
# too, so fixing one sentence and not the other cannot pass.
#
# The no-converter shim is what makes this state reachable at all: with a working iconv
# the tail is squeezed (or its status is caught) and this branch is never entered.
python3 -c 'import sys,pathlib; pathlib.Path(sys.argv[1]).write_text("\u2192\u2192\u2192\n", encoding="utf-8")' "$FIX/drift-report-nonascii.txt"
# The pre-check below reproduces the SCRIPT's own assignment shape on purpose: a command
# substitution strips trailing newlines, so `→→→\n` captures as EMPTY even though one LF
# survives the byte delete. Testing the delete alone (`| wc -c` = 1) would describe a
# different state than the branch actually reaches. ⚠️ This check is a DIAGNOSTIC, not the
# non-vacuity anchor: it copies the expression, so if the script's capture shape changes it
# can keep passing while describing a state the code no longer reaches. The POSITIVE body
# rows below are the anchor — `produced no excerpt either` is emitted ONLY in the
# empty-fallback arm (the script's own wording, quoted exactly: an earlier draft of this
# comment quoted a phrase — `yielded NOTHING either` — that no commit of this branch ever
# emitted, which is this PR's own class one layer down), so a fixture that stopped reaching
# that arm reds those rows regardless of this check.
EX17I="$(LC_ALL=C tr -d '\200-\377' < "$FIX/drift-report-nonascii.txt")"
if [ -z "$EX17I" ]; then
  ok "case 17i: the fixture captures as EMPTY under the ASCII fallback (so the case is not vacuous)"
else
  bad "case 17i: the fixture does NOT capture as empty — this case cannot reach the state it pins (got '$EX17I')"
fi
export PATH="$ICONV_BROKEN_DIR:$PATH_BEFORE_ICONV_SHIM"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/drift-report-nonascii.txt"
export PATH="$PATH_BEFORE_ICONV_SHIM"
assert_eq "$RC" "0" "case 17i: a report with no ASCII left still files the alert"
assert_eq "$(count_creates)" "1" "case 17i: and an issue IS filed — the ledger leg never goes deaf"
BODY_17I="$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)"
assert_contains "$BODY_17I" "could not be byte-squeezed" "case 17i: and the squeeze failure is still STATED"
assert_contains "$BODY_17I" "produced no excerpt either" "case 17i: and it names the state it is actually in"
assert_contains "$BODY_17I" "run link above" "case 17i: and sends the reader to where the report actually is"
assert_not_contains "$BODY_17I" "it is pasted with its non-ASCII bytes dropped" "case 17i: and it does NOT claim a pasted excerpt it does not carry (#12200 — this row reds if the sentence's condition is removed)"
assert_not_contains "$BODY_17I" "only its last 6000 were read" "case 17i: and it does NOT declare a cut for a report under the cap — the clause is CONDITIONAL (this report is a few bytes)"
# The claim is pinned by SHAPE, not only by one phrasing, and the shape row alone is NOT the
# net: round-1 review measured a mutant that keeps every other row green — the correct state
# sentence SURVIVES and a second clause is added ("… yet the report **was** pasted with its
# non-ASCII bytes dropped"). A past-tense verb walked past the original alternation, so the
# row now spans the tenses AND the PRESENCE verbs this paragraph can carry (pasted / reproduced
# / quoted / shown …: "the report was reproduced below" is the same false claim with a
# different verb), and a verb-agnostic row pins the claim's distinctive clause (the loss
# declaration), which no synonym for "is pasted" can avoid — the two nets catch different
# regressions and BOTH are kept.
# ⚠️ BOTH nets match against a NEWLINE-FLATTENED body (review round 2, P3): this script writes each
# sentence as ~90-char `echo` lines, and `assert_no_match` greps line by line — so a claim whose
# subject and verb land on DIFFERENT lines (`echo "the report is"` / `echo "reproduced below."`)
# escaped both nets while the rendered markdown joins them into the claim. Flattening makes the nets
# match what the READER sees, not what the source happens to wrap on.
# ⚠️ The subject list is why neither row false-reds on the CORRECT wording: the fixed empty-state
# sentence says "NOTHING is pasted below", whose subject is `NOTHING` — never one of the claim
# subjects above. A rewrite that gives the negation a listed subject WOULD false-red, and that is
# the correct trade: the state sentence is ours to word.
BODY_17I_FLAT="$(printf '%s' "$BODY_17I" | tr '\n' ' ')"
# ⛔ Case-INSENSITIVE for the shape row, because a claim can start a sentence ("The report is
# reproduced below.") — measured: the capitalised split-line variant walked past a lowercase-only
# ERE. The correct wording is safe under lowercasing: its subject is `nothing`.
BODY_17I_LC="$(printf '%s' "$BODY_17I_FLAT" | tr '[:upper:]' '[:lower:]')"
assert_no_match "$BODY_17I_LC" '(it|they|this|the excerpt|the report|the degraded report) (is|are|was|were|has been|have been) +(pasted|reproduced|quoted|included|shown|carried|printed)' "case 17i: no phrasing of a PASTE/PRESENCE claim survives (#12200), across line breaks and case"
assert_not_contains "$BODY_17I_FLAT" "with its non-ASCII bytes dropped" "case 17i: and no LOSS CLAUSE survives either — verb-agnostic, so a synonym for 'pasted' cannot carry it in"
assert_no_match "$BODY_17I" 'follows verbatim' "case 17i: and the section intro claims no verbatim report either — the louder sibling of the same class"
# ⛔ THE INTRO'S CONDITION IS THE FIX, SO THE CONDITION IS WHAT GETS PINNED (review round 3, P2).
# The rows above pin the PHRASE that had to go; nothing pinned the clause that makes the
# replacement true. Measured: delete `when it can be carried` and this file stays green,
# 186/186 — while the body then reads "… is reproduced below;" in a state whose next
# paragraph says "NOTHING is pasted below", i.e. the exact claim #12200 exists to remove,
# restored by a four-word edit. The net below is the shape that catches that deletion and
# NOT the correct wording: it needs the presence verb to be followed by END-OF-CLAIM
# punctuation, so "reproduced below when it can be carried;" (the shipped sentence, and
# any rewording that keeps a condition) does not match, while "reproduced below;" does.
# ⚠️ Why it is NOT the SHAPE net reused from :739: that row requires SUBJECT-VERB ADJACENCY,
# and the intro separates them with an appositive ("the one that names … — is reproduced
# below"), so the intro's own sentence shape walks past it. `pasted` is deliberately NOT in
# this verb list: the CORRECT empty-state sentence ends "NOTHING is pasted below;", which
# this net would otherwise false-red.
BODY_17I_PRESENCE='(reproduced|quoted|shown|included|printed|carried) below([[:space:]]*[;.,:]|$)'
assert_no_match "$BODY_17I_LC" "$BODY_17I_PRESENCE" "case 17i: and the intro's presence claim is CONDITIONED — an unconditional '<verb> below;' would claim a report this state does not carry (#12200)"
# …and the SAME claim with a word between `below` and its punctuation walks past the row above
# (measured in review round 4: `reproduced below in full;` left 190/190 green while the body
# claimed a report in a state that carries none). So the condition is pinned POSITIVELY as
# well — the presence claim must be followed by a conditional keyword, whatever its wording.
# Residual, stated rather than implied: a restructure that moves the condition BEFORE the verb
# ('when it can be carried, the report is reproduced below') reds this row although the
# sentence is true. That is the right failure mode (a red asks for the alternation to be
# extended) as against a green that certifies a claim the body does not carry.
BODY_17I_CONDITION='(reproduced|quoted|shown|included|printed|carried) below[[:space:]]+(when|whenever|if|provided|unless)'
assert_match "$BODY_17I_LC" "$BODY_17I_CONDITION" "case 17i: and the condition is PRESENT in the claim — 'below in full;' is unconditional, so it would read as a carried report (review round 4)"
assert_not_contains "$BODY_17I" '```' "case 17i: and it pastes NO fenced block, in any form"
if python3 -c "import sys,pathlib; pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')" "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null; then
  ok "case 17i: and the body is still valid UTF-8"
else
  bad "case 17i: the body is NOT valid UTF-8"
fi

# ── case 17j: a converter's own diagnostic must not reach the job log (#12200) ────────
# The fallback also fires when `iconv` is ABSENT, and a missing or refusing converter
# prints its own diagnostic. This branch ADDS the suppression relative to main (#12200,
# "Also in the same block"). The history, checked against the commits rather than assumed:
# the pre-#12110 form of this statement DID suppress it (`… -c 2>/dev/null || true`,
# 49cf519354), and #12110's rewrite of the squeeze (6e5ff01625) did not carry it over — no
# merge dropped it, and `git log 6e5ff01625..origin/main -- …deploy-api-alert.sh` is empty,
# so the branch does not inherit it from anywhere. (An earlier draft of this comment said a
# merge dropped it, which is the same class this PR removes: a sentence stating a mechanism
# the artifact does not show.) One spurious stderr line is not a correctness bug — but it is
# noise on the one channel an operator reads during an incident, and an unsuppressed
# diagnostic reads like a second failure. Pinned, or a later edit drops it and nothing says so.
ICONV_NOISY_DIR="$FIX/bin-iconv-noisy"
mkdir -p "$ICONV_NOISY_DIR"
cat > "$ICONV_NOISY_DIR/iconv" <<'ICONV_NOISY_EOF'
#!/usr/bin/env bash
# Refuses AND says so on stderr — the shape a missing/failing converter has on a runner.
echo "iconv: I refuse this conversion" >&2
exit 1
ICONV_NOISY_EOF
chmod +x "$ICONV_NOISY_DIR/iconv"
export PATH="$ICONV_NOISY_DIR:$PATH_BEFORE_ICONV_SHIM"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/drift-report-utf8.txt"
export PATH="$PATH_BEFORE_ICONV_SHIM"
assert_eq "$RC" "0" "case 17j: a converter that FAILS noisily still files the alert"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "could not be byte-squeezed" "case 17j: and the body still declares the fallback"
assert_not_contains "$OUT" "I refuse this conversion" "case 17j: and its diagnostic is SUPPRESSED — the job log carries the alert, not the converter's complaint"

# ── case 17b (arm c): the disclosure is a BYTE MEASURE, not the status ─────────────────
# glibc 2.39 drops the split sequence and exits 0, so a status-based disclosure says
# nothing on every ordinary dev box while the excerpt IS short. This arm runs the REAL
# iconv — whichever generation this host has — and requires the drop to be disclosed
# anyway. Without it, and without arm (c) above, the disclosure would be pinned only on
# the one host generation that happens to report the drop in its exit status.
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/drift-report-utf8.txt"
assert_eq "$RC" "0" "a real iconv files the alert on either glibc generation"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "NOT byte-identical" "and the characters it dropped SILENTLY are still disclosed (the measure, not the status)"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "BLOCKING repo-ahead migrations" "and the squeezed excerpt is carried"

# ── case 17c: a report with no excerpt-able text says only what is TRUE ─────────
# A report of 6000 line endings (or NUL bytes) captures as EMPTY, so there is no
# excerpt to paste. The earlier shape of this branch claimed BOTH "could not be
# byte-squeezed" AND "the report could not be re-read at all" in that state, and pasted
# an empty fenced block under a heading that promises the report — three statements
# that were all false for a file that was read perfectly well (measured, review
# round 3). It must say only what is true, and paste no fence.
python3 -c 'import sys,pathlib; pathlib.Path(sys.argv[1]).write_bytes(b"\n" * 6000)' "$FIX/drift-report-blank.txt"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/drift-report-blank.txt"
assert_eq "$RC" "0" "a report holding no excerpt-able text still files the alert"
BLANK_BODY="$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)"
assert_contains "$BLANK_BODY" "held no excerpt-able text" "and says the report held no excerpt-able text"
assert_not_contains "$BLANK_BODY" "could not be byte-squeezed" "and does NOT claim a squeeze it never attempted"
assert_not_contains "$BLANK_BODY" '```' "and pastes NO fenced block when there is nothing to paste"
# The section intro is emitted BEFORE the report is read, so an unconditional claim about
# what follows it is false in every report-less state — the blank, 0-byte, unreadable and
# fallback-empty bodies alike (#12200). Pinned in this state as well as 17i so the claim
# cannot survive in one arm while being fixed in another.
assert_no_match "$BLANK_BODY" 'follows verbatim' "and the intro claims no VERBATIM report either (#12200)"
# …and the intro's CONDITION is pinned in this state too, not only in 17i (review round 3,
# P2): the same four-word deletion must not be able to survive in one report-less arm while
# reddening in the other. Same net, flattened+lowercased for the same reasons as 17i's.
BLANK_BODY_LC="$(printf '%s' "$BLANK_BODY" | tr '\n' ' ' | tr '[:upper:]' '[:lower:]')"
assert_no_match "$BLANK_BODY_LC" "$BODY_17I_PRESENCE" "and the intro's presence claim is CONDITIONED here too — 'reproduced below;' would claim a report a blank state does not carry (#12200)"
assert_match "$BLANK_BODY_LC" "$BODY_17I_CONDITION" "and that claim CARRIES its condition here too — the positive half, so an adverb after 'below' cannot smuggle the unconditional claim back in"

# ── case 17k: a BIG report whose TAIL is blank — declare the cut and say which bytes
#    were read (review round 5, P3) ────────────────────────────────────────────────
# Only the report's LAST 6000 bytes are read, so a 15,400-byte report whose final bytes are
# blank captures as EMPTY while the FILE holds plenty of text. Calling that "the report file
# held no excerpt-able text" is false about the file — and it left the cut undeclared, which
# made the section intro's "any of the 6000-byte cut … is declared beside it" false in this
# single state. Both halves are pinned: the sentence must attribute the emptiness to the TAIL
# it actually read, name the size, and the unqualified sentence must be absent.
python3 -c 'import sys,pathlib; pathlib.Path(sys.argv[1]).write_bytes((b"check-migration-drift: BLOCKING 20260926000002\n" * 200) + (b"\n" * 6000))' "$FIX/drift-report-bigtail.txt"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/drift-report-bigtail.txt"
assert_eq "$RC" "0" "case 17k: a >6000-byte report with a blank tail still files the alert"
BIGTAIL_BODY="$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)"
assert_contains "$BIGTAIL_BODY" "last 6000 bytes held no excerpt-able text" "case 17k: and it says the TAIL held nothing, not that the FILE did"
assert_not_contains "$BIGTAIL_BODY" "**The report file held no excerpt-able text**" "case 17k: and the unqualified sentence — false about a 15,400-byte file — is ABSENT"
assert_contains "$BIGTAIL_BODY" "15400 bytes" "case 17k: and it names the report's real size, so a reader sees text above the cut"
assert_contains "$BIGTAIL_BODY" "only that tail is read" "case 17k: and the 6000-byte cut is DECLARED here too — the intro promises it beside the excerpt"
assert_not_contains "$BIGTAIL_BODY" '```' "case 17k: and it pastes no fenced block it does not have"

# ── case 17l: a HEALTHY converter that emits nothing is NOT a missing converter
#    (review round 5, note 1) ───────────────────────────────────────────────────
# The empty-fallback sentence used to name two causes — no `iconv`, or one that refused — while
# a THIRD is reachable and needs no broken converter at all: the tail's last 6000 bytes are
# entirely undecodable, so `iconv -c` emits ZERO bytes and the ASCII fallback finds nothing to
# keep either. ⚠️ Its STATUS there is 1, not 0 (measured on glibc 2.39: `printf '\x92' | iconv
# -c` → rc 1, 0 bytes; `printf 'A\xffB' | iconv -c` → rc 0, 2 bytes, so a converter returns 0
# only when it emitted something) — an earlier draft of this comment said it "succeeds", which
# is false on every host the suite runs on, and the alert does not read that status (`if !
# excerpt=…; then :`). On such a host the old sentence told the on-call reader the converter
# was missing while it had just run. The row pins the STATE ("emitted nothing it could convert")
# and the absence of the two-cause form; it is a phrase-level pin, which is the residual #12283
# records for this whole harness.
python3 -c 'import sys,pathlib; pathlib.Path(sys.argv[1]).write_bytes((b"check-migration-drift: BLOCKING 20260926000002\n" * 200) + (b"\x92" * 6000))' "$FIX/drift-report-undecodable-tail.txt"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/drift-report-undecodable-tail.txt"
assert_eq "$RC" "0" "case 17l: an all-undecodable tail still files the alert"
UNDEC_BODY="$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)"
assert_contains "$UNDEC_BODY" "produced no excerpt either" "case 17l: and it names the state it is in"
assert_not_contains "$UNDEC_BODY" "is unavailable or refused to emit" "case 17l: and does NOT blame the converter — a healthy converter that emits nothing is not a missing one"
assert_contains "$UNDEC_BODY" "emitted nothing it could convert" "case 17l: and the third cause is named, so the enumeration matches what the host can do"
assert_not_contains "$UNDEC_BODY" '```' "case 17l: and it pastes no fenced block"
assert_contains "$UNDEC_BODY" "only its last 6000 were read" "case 17l: and the 6000-byte cut is DECLARED in this arm too (review round 6 — the intro promises it beside the excerpt)"
assert_contains "$UNDEC_BODY" "15400 bytes" "case 17l: and it names the report's real size"

# ── case 17m: the ASCII FALLBACK's own diagnostic must not reach the job log (round 7) ─
# The same rationale that put `2>/dev/null` on the `iconv` squeeze applies one line down: an
# absent or failing `tr` prints its own diagnostic, which lands in the job log beside an alert
# that went out perfectly well. R3 named "the converter", so this is the same class just
# outside its letter — measured: `tr: command not found` reached the run's stderr.
TR_NOISY_DIR="$FIX/bin-tr-noisy"
mkdir -p "$TR_NOISY_DIR"
cat > "$TR_NOISY_DIR/tr" <<TR_NOISY_EOF
#!/usr/bin/env bash
# Fails ONLY the ASCII-fallback squeeze (its own \`-d\` flag) and does so noisily.
for a in "\$@"; do
  if [ "\$a" = "-d" ]; then
    echo "tr: cannot delete" >&2
    exit 1
  fi
done
exec "$REAL_TR" "\$@"
TR_NOISY_EOF
chmod +x "$TR_NOISY_DIR/tr"
reset_case
export PATH="$TR_NOISY_DIR:$PATH_BEFORE_ICONV_SHIM"
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/drift-report-undecodable-tail.txt"
export PATH="$PATH_BEFORE_ICONV_SHIM"
assert_eq "$RC" "0" "case 17m: a FAILING, NOISY ASCII fallback still files the alert"
assert_not_contains "$OUT" "tr: cannot delete" "case 17m: and its diagnostic is SUPPRESSED — the job log carries the alert, not the fallback's complaint"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "produced no excerpt either" "case 17m: and the body still names the state it is in"

# ── case 17d: the READ GUARD — an existing but unreadable report must not abort ─────
# NOTE THE MECHANISM (corrected, pass-5 review): a DIRECTORY is `-s`-TRUE on this fs, so
# this case would have reached the read even before the `-e`/`-p` widening. What it pins is
# the guarded READ (`|| raw_tail=""`): without it `tail` fails under `set -euo pipefail`
# and the alert exits 1 having filed NOTHING — the exact #12077 class, at the line round 2
# moved the read back onto. The widening's OWN behaviour is pinned by 17e (0-byte report)
# and 17f (FIFO).
mkdir -p "$FIX/report-dir"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/report-dir"
assert_eq "$RC" "0" "case 17d: an existing but UNREADABLE report does not abort the alert"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "held no excerpt-able text" "case 17d: and the body says the report yielded no excerpt"
assert_eq "$(count_creates)" "1" "case 17d: and the ledger issue was still filed"

# ── case 17e: a 0-byte report is READABLE and EMPTY, and must be described so ───────
# Under the old `[ -s … ]` guard a 0-byte report was described as "the gate's report file
# was **not readable**" — false: it is readable and empty. `-e` is what changes that, so
# this case is what pins the widening (a directory does not: it is `-s`-true).
: > "$FIX/report-empty.txt"
reset_case
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/report-empty.txt"
assert_eq "$RC" "0" "case 17e: a 0-byte report files the alert"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "held no excerpt-able text" "case 17e: and is described as holding no excerpt"
assert_not_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "was not readable" "case 17e: and NOT as 'not readable' — it IS readable, it is empty"
assert_eq "$(count_creates)" "1" "case 17e: and the ledger issue was filed"

# ── case 17f: a FIFO must not HANG the alert ────────────────────────────────────────
# `-e` alone admits a FIFO (st_size 0), and `tail -c` then blocks in open(2) until a writer
# appears: measured SIGTERM at 6 s, no ledger entry and no exit status. No caller produces
# one; this pin is what keeps the next caller from turning a decorative excerpt into a hang.
mkfifo "$FIX/report-fifo"
reset_case
# `timeout` is NOT on the fleet's macOS dev hosts (pyproject.toml records it absent from PATH,
# /opt/homebrew/bin and /usr/local/bin; tools/ready_for_owner.py does its own bounding for the
# same reason). A hard `timeout` here would red such a host on correct code — a host-dependent
# red for a reason unrelated to the property, which is the class this PR exists to remove. The
# pin stays unconditional in CI (ubuntu-latest has it); elsewhere the skip is DECLARED, not
# silent. A hand-rolled `kill -9` fallback is deliberately not used: it loses `timeout`'s
# process-group kill and would leave the blocked `tail` behind — reintroducing the hang.
if command -v timeout >/dev/null 2>&1; then
  timeout 20 "$HELPER" --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/report-fifo" >/dev/null 2>&1
  assert_eq "$?" "0" "case 17f: a FIFO report does not hang the alert (a hang returns 124 here)"
  assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "was NOT READ" "case 17f: and a FIFO is described as NOT READ — it IS readable, reading it is what would block (review round 7)"
  assert_not_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "was not readable" "case 17f: and NOT as 'not readable', which is false of a pipe a reader can drain"
  assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null)" "is a named pipe" "case 17f: and it names the real property — a pipe, which could block"
else
  ok "case 17f: SKIPPED on this host — no \`timeout\` binary (macOS); the FIFO guard is pinned by CI (ubuntu-latest)"
fi

# ── case 17g: main's own #12077 pin — the same two arms, converter-independent ────────
# Kept from `main` (it landed the same fix independently) and RENUMBERED: this block
# and case 17c above both used to be called "17c". The overlap with 17b is deliberate
# — 17b wraps the HOST's real `iconv`, so on a host without one its arm (a) refuses to
# pass vacuously, while this block stubs `iconv` outright and therefore runs everywhere.
# Both shapes pin the same two properties: a non-zero status must not abort the alert,
# and a converter that emits NOTHING must leave a VALID UTF-8 body that still carries
# the report (17b's fallback arm pins the same invariant from the other side).
# `iconv -c` guarantees valid UTF-8 OUTPUT; its EXIT STATUS is not part of that
# contract (GNU libiconv, and newer glibc builds, return 1 having dropped an
# invalid sequence). Under `set -e` + `pipefail` that status killed the notifier
# before the body was built, so NOTHING was filed — observed on CI as
# "the alert succeeds on a report cut mid-character (got '1', want '0')". A
# fail-closed notifier is precisely the silence this script exists to remove, so
# both shapes are pinned here: status 1 WITH valid output, and status 1 with no
# output at all (where the ASCII fallback must still carry the remediation).
# The stub shadows the real iconv through the harness PATH, so this case does
# not depend on the host's iconv build.
cat > "$BIN/iconv" <<'ICONV_EOF'
#!/usr/bin/env bash
# STUB_ICONV_MODE=status → what `iconv -c` REALLY does: emit the CONVERTIBLE bytes
# (valid UTF-8), then report "a sequence was dropped" in the STATUS.  =silent →
# report it and emit nothing.
[ "${STUB_ICONV_MODE:-status}" = "silent" ] && exit 1
# ⛔ `cat` here modelled the WRONG converter (review round 3, P2): it passed the
# ORPHANED continuation byte through, so the pasted excerpt was invalid UTF-8 —
# the artifact case 17 forbids and 17b arm (a) forbids explicitly — while the
# row went green, because nothing in this case measured the body's validity. The
# comment above claimed "status 1 WITH valid output" about a stub that emits
# invalid output. Decoding with errors ignored IS `-c`'s contract.
python3 -c 'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read().decode("utf-8","ignore").encode())'
exit 1
ICONV_EOF
chmod +x "$BIN/iconv"
reset_case
export STUB_ICONV_MODE=status
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/drift-report-utf8.txt"
assert_eq "$RC" "0" "an iconv status of 1 with valid output does not abort the alert (the notifier is the safety net)"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "gh workflow run supabase-deploy.yml --ref main" "and the excerpt still reaches the body"
# …and the two properties 17b arm (a) pins ONLY where a real `iconv` exists are pinned here,
# converter-independently — which is this case's whole reason to exist (review round 3, P2:
# before these rows the label said "with valid output" and nothing measured it).
assert_not_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "could not be byte-squeezed" "case 17g: and it is the SQUEEZED excerpt, not the raw fallback"
if python3 -c "import sys,pathlib; pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')" "$RUNNER_TEMP/deploy-api-alert-body.md" 2>/dev/null; then
  ok "case 17g: and the status-1 body is VALID UTF-8 — the split character was dropped, never pasted"
else
  bad "case 17g: the status-1 body is NOT valid UTF-8 — the raw tail's orphaned byte reached the issue"
fi
reset_case
export STUB_ICONV_MODE=silent
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$FIX/drift-report-utf8.txt"
assert_eq "$RC" "0" "an iconv status of 1 with NO output does not abort the alert"
UTF8_FALLBACK_BODY="$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")"
assert_contains "$UTF8_FALLBACK_BODY" "gh workflow run supabase-deploy.yml --ref main" "the ASCII fallback still carries the report (not an empty fenced block)"
if printf '%s' "$UTF8_FALLBACK_BODY" | python3 -c "import sys; sys.stdin.buffer.read().decode('utf-8')" 2>/dev/null; then
  ok "the fallback body is valid UTF-8 (non-ASCII bytes are dropped, never split)"
else
  bad "the fallback body is NOT valid UTF-8"
fi
unset STUB_ICONV_MODE
rm -f "$BIN/iconv"   # every later case must see the real converter again

# A job with no declared note still alerts, with an honest generic sentence
# rather than a wrong one (the workflow's site set is pinned by pytest).
reset_case
run_alert --job some-other-job --steps "whatever=failure"
assert_eq "$RC" "0" "an undeclared job still alerts"
assert_contains "$(cat "$RUNNER_TEMP/deploy-api-alert-body.md")" "read the run" "and gets the generic sentence, not a wrong one"

echo "── case 14: usage errors ───────────────────────────────────────────"
reset_case
run_alert --steps "$DRIFT_STEPS"
assert_eq "$RC" "2" "--job is required"
run_alert --job deploy-api --bogus x
assert_eq "$RC" "2" "an unknown argument is a usage error"
assert_eq "$(count_calls 'GH')" "0" "a usage error contacts nothing"
# A value-taking flag with no value must be a usage error WITH a message, not a
# bare `shift 2` failure under `set -e` (exit 1, no output). (Round-5, P3-1.)
for flag in --job --steps --drift-report; do
  reset_case
  run_alert "$flag"
  assert_eq "$RC" "2" "$flag with no value is a usage error"
  assert_contains "$OUT" "needs a value" "and says so, instead of exiting 1 silently"
done
# `--help` documents itself and succeeds. This used to exit 2: `usage` returns 2
# and `set -e` aborted before the `return 0` beside it could run. (Round-4
# review, P3.)
reset_case
run_alert --help
assert_eq "$RC" "0" "--help exits 0, as it claims"
assert_eq "$(count_calls 'GH')" "0" "--help contacts nothing"

# ── #8841: the RESOLVE leg — retiring a recovered finding's ledger key ──────
# Filing without closing is a ledger that can only grow. Measured on this
# workflow's own `drift` key: #7634 was filed 2026-10-07T20:11Z, **21 runs then
# SUCCEEDED** on it, and ten days later the issue was still OPEN carrying 238
# `Recurrence` comments — the standing alert claimed a condition that was no
# longer true. These cases pin the closing half: only a step's OWN `success` in
# this run retires its key, `skipped` is never a pass, the job-level key needs
# `job.status == success`, and the leg can never redden a green deploy.

echo
echo "── case 19: every passing step retires its key; a green job retires the job key ──"
reset_case
export STUB_SEARCH_DYNAMIC=1
run_alert --job deploy-api --resolve --job-status success \
  --steps "parity=success drift=success deploy=success"
assert_eq "$RC" "0" "the resolve leg exits 0 on a green run"
assert_eq "$(count_closes)" "4" "3 passing steps + the job-level key are all retired"
assert_contains "$(cat "$STUB_TMP/searched.txt")" "deploy-hosted: deploy-api failed at 'drift'" "the drift key is retired by its EXACT title"
assert_contains "$(cat "$STUB_TMP/searched.txt")" "deploy-hosted: deploy-api failed at 'parity'" "and every other passing step's key"
# The job-level fallback key carries no step id.
assert_match "$(cat "$STUB_TMP/searched.txt")" '^deploy-hosted: deploy-api failed$' "the job-level key (no step id) is retired when the JOB is green"
assert_eq "$(count_creates)" "0" "the resolve leg never files an issue"
assert_eq "$(count_calls 'CURL')" "0" "and never pages (this is bookkeeping, not an alert)"
assert_eq "$(count_calls 'GH POST .*/comments$')" "4" "each retirement records WHY on the issue"

echo
echo "── case 20: a FAILED step is not retired, and a red job keeps its job key ──"
reset_case
export STUB_SEARCH_DYNAMIC=1
run_alert --job deploy-api --resolve --job-status failure \
  --steps "parity=success drift=success deploy=failure"
assert_eq "$RC" "0" "the resolve leg still exits 0 (never reddens the run)"
assert_eq "$(count_closes)" "2" "only the two PASSING steps are retired"
assert_contains "$(cat "$STUB_TMP/searched.txt")" "at 'parity'" "the passing step is retired"
assert_not_contains "$(cat "$STUB_TMP/searched.txt")" "at 'deploy'" "the FAILING step's streak is NOT retired"
assert_no_match "$(cat "$STUB_TMP/searched.txt")" '^deploy-hosted: deploy-api failed$' "the job-level key is NOT retired while the job is red"
assert_eq "$(count_creates)" "0" "and nothing was filed"

echo
echo "── case 21: SKIPPED is not a pass (a run that dies early skips the rest) ──"
reset_case
export STUB_SEARCH_DYNAMIC=1
run_alert --job deploy-api --resolve --job-status failure \
  --steps "drift=success fly-machines=skipped set-secrets=skipped deploy="
assert_eq "$RC" "0" "exit 0"
assert_eq "$(count_closes)" "1" "only the one step that actually PASSED is retired"
assert_contains "$(cat "$STUB_TMP/searched.txt")" "at 'drift'" "the passed step"
assert_not_contains "$(cat "$STUB_TMP/searched.txt")" "at 'fly-machines'" "a skipped step is never read as a pass"
assert_not_contains "$(cat "$STUB_TMP/searched.txt")" "at 'deploy'" "nor an outcome-less (never-reached) step"

echo
echo "── case 22: a failed close is a WARNING, never a red deploy ──────────────"
reset_case
export STUB_SEARCH_DYNAMIC=1
export STUB_CLOSE_FAIL=1
run_alert --job deploy-api --resolve --job-status success --steps "drift=success"
assert_eq "$RC" "0" "a bookkeeping failure does NOT fail the job (a red here would be a FALSE deploy failure)"
assert_contains "$OUT" "could NOT resolve" "the failure is surfaced on every run instead of being silent"
assert_contains "$OUT" "stays OPEN" "and names the standing consequence"

echo
echo "── case 23: an untrusted search closes NOTHING (fail closed) ─────────────"
reset_case
export STUB_SEARCH_DYNAMIC=1
export STUB_SEARCH_FAIL=1
run_alert --job deploy-api --resolve --job-status success --steps "drift=success"
assert_eq "$RC" "0" "still exit 0 (it cannot redden a green deploy)"
assert_eq "$(count_closes)" "0" "'cannot see the ledger' is never 'resolved'"
assert_contains "$OUT" "could NOT resolve" "and it says so loudly"

echo
echo "── case 24: resolve mode needs a token but must NOT red the job ──────────"
reset_case
unset GH_TOKEN GITHUB_TOKEN || true
run_alert --job deploy-api --resolve --job-status success --steps "drift=success"
assert_eq "$RC" "0" "a missing token is a warning on the green path, not a false deploy failure"
assert_contains "$OUT" "GH_TOKEN is not set" "the missing token is named"
assert_contains "$OUT" "stays OPEN" "and so is the consequence"
assert_eq "$(count_calls 'GH')" "0" "nothing was contacted"
export GH_TOKEN="test-token"

echo
echo "── case 25: the ALERT path never closes anything ────────────────────────"
reset_case
export STUB_SEARCH_DYNAMIC=1
run_alert --job deploy-api --steps "$DRIFT_STEPS" --drift-report "$DRIFT_REPORT"
assert_eq "$RC" "0" "the alert path still succeeds"
assert_eq "$(count_closes)" "0" "a FAILING run must never retire a key"
assert_eq "$(count_comments)" "1" "it records the occurrence instead"

echo
echo "── case 26: usage — --resolve/--job-status validation ───────────────────"
reset_case
run_alert --job deploy-api --job-status success
assert_eq "$RC" "2" "--job-status without --resolve is a usage error (it would silently do nothing)"
assert_contains "$OUT" "only meaningful with --resolve" "and says so"
run_alert --job deploy-api --resolve --job-status bogus
assert_eq "$RC" "2" "an unknown --job-status is a usage error"
run_alert --job deploy-api --resolve --job-status
assert_eq "$RC" "2" "--job-status with no value is a usage error"
assert_contains "$OUT" "needs a value" "and says so"
assert_eq "$(count_calls 'GH')" "0" "a usage error contacts nothing"

echo
if [ "$FAIL" -eq 0 ]; then
  echo "✅ deploy-api-alert.test.sh — $PASS assertions passed"
  exit 0
fi
echo "❌ deploy-api-alert.test.sh — $FAIL of $((PASS + FAIL)) assertions failed"
exit 1
