#!/usr/bin/env bash
# published-artifact-alert.test.sh — harness for the release-artifact-canary alert (#12363).
#
# WHY THIS EXISTS. Every sibling monitor on the shared auto-file substrate ships a
# `.test.sh` that ci.yml's `ops-harnesses` job runs (auto-file-issue, deploy-api-alert,
# availability-watchdog, welcome-e2e-monitor …). This alert originally shipped with NO
# executable test at all — its only pin was a `grep` for the string "gh issue create" in
# the Python suite, which cannot tell a working alert from one that files nothing — and
# it re-implemented dedupe, `Recurrence #N` and close-then-comment inline instead of
# riding the substrate (#12363 round 6).
#
# WHAT IT PINS
#   1. delegation: `open` calls the substrate's `file` verb with the canary's stable
#      title (the dedupe key) and `close` calls `resolve` — a local re-implementation is
#      the defect.
#   2. the EXIT-CODE BRANCH: exit 2 (detector outage) must never produce a body that
#      tells a human the artifact is broken; exit 1 must name the release action.
#   3. the TRANSCRIPT TAIL: the last lines (where `tortoise init` and `[exit N]` live)
#      reach the issue body, not the venv/pip preamble.
#   4. fail-closed on a missing token: a red run that cannot file must say so, never
#      exit 0 as if it filed.
#   5. `close` is a no-op with no plumbing (a green run must never be turned red).
#   6. an unknown verb is a usage error, never a silent no-op.
#
# Self-contained: no network, no secrets, no real `gh`. The script is SOURCED (not
# executed) and its two substrate wrappers are redefined to record, so this file tests
# OUR contract while auto-file-issue.sh's own harness remains the authority for dedupe /
# `Recurrence #N` / refuse-on-failed-search.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1

fail=0
note() {
  echo "  ✗ $*"
  fail=1
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export RECORDER="$tmp/record"

# ── source the script under test, then swap its substrate wrappers for recorders ─────
# shellcheck source=/dev/null
source .github/scripts/published-artifact-alert.sh

af_file() {
  printf 'VERB=file\n' >"$RECORDER"
  while [ $# -gt 0 ]; do
    case "$1" in
      --title) printf 'TITLE=%s\n' "$2" >>"$RECORDER"; shift 2 ;;
      --body-file)
        printf 'BODY_FILE=%s\n' "$2" >>"$RECORDER"
        cp "$2" "$RECORDER.body"
        shift 2
        ;;
      --label) printf 'LABEL=%s\n' "$2" >>"$RECORDER"; shift 2 ;;
      *) printf 'UNKNOWN=%s\n' "$1" >>"$RECORDER"; shift ;;
    esac
  done
}

af_resolve() {
  # APPEND, not truncate: `close` resolves TWO ledgers and a per-call truncation made the
  # harness unable to see either one missing (#12363 round 8).
  printf -- '---\nVERB=resolve\n' >>"$RECORDER"
  while [ $# -gt 0 ]; do
    case "$1" in
      --title) printf 'TITLE=%s\n' "$2" >>"$RECORDER"; shift 2 ;;
      --why) printf 'WHY=%s\n' "$2" >>"$RECORDER"; shift 2 ;;
      *) printf 'UNKNOWN=%s\n' "$1" >>"$RECORDER"; shift ;;
    esac
  done
}

run_open() {
  rm -f "$RECORDER" "$RECORDER.body"
  EXIT_CODE="$1" GH_TOKEN="fake-token" GITHUB_REPOSITORY="daniel-ospina/tortoise" \
    GITHUB_RUN_ID="12345" RUNNER_TEMP="$tmp" \
    open_issue >/dev/null 2>&1
  echo $?
}

# ── 1. delegation + the exit-1 body ──────────────────────────────────────────────────
mkdir -p "$tmp/canary"
{
  echo "\$ /tmp/x/venv/bin/pip install --quiet tortoise-graph==0.2.0   # install"
  echo "[exit 0]"
  echo "\$ /tmp/x/venv/bin/tortoise init   # documented first command"
  echo "*** FATAL CONFIG FILE ERROR (Redis 8.10.2) ***"
  echo ">>> 'dir /home/u/.tortoise'' No such file or directory"
  echo "[exit 1]"
} >"$tmp/canary/published-artifact-canary.log"

rc="$(run_open 1)"
[ "$rc" = "0" ] || note "open exited $rc, expected 0"
grep -q '^VERB=file$' "$RECORDER" || note "open did not delegate to the substrate's 'file' verb"
grep -q '^TITLE=release-artifact-canary: published PyPI artifact is red$' "$RECORDER" ||
  note "open did not pass the canary's stable title (the dedupe key)"
grep -q '^LABEL=bug$' "$RECORDER" || note "open did not pass the bug label"

# ── 2. the exit-code branch ─────────────────────────────────────────────────────────
grep -q 'clears when a release lands' "$RECORDER.body" ||
  note "exit 1 body does not name the release action — the finding has no owner"
if grep -qi 'not\*\* evidence the artifact is broken' "$RECORDER.body"; then
  note "exit 1 body claims a detector outage"
fi

rc="$(run_open 2)"
[ "$rc" = "0" ] || note "open (exit 2) exited $rc, expected 0"
grep -qi 'not\*\* evidence the artifact is broken' "$RECORDER.body" ||
  note "exit 2 body does not say the artifact is NOT implicated"
grep -q '^TITLE=release-artifact-canary: could not evaluate the published artifact$' "$RECORDER" ||
  note "a detector outage must get its OWN ledger — sharing the release title lets one severity absorb the other's recurrence"
if grep -q 'clears when a release lands' "$RECORDER.body"; then
  note "exit 2 body tells a human to cut a release on a detector outage"
fi

# ── 2c. a genuine release finding disproves an earlier outage ──────────────────────
rm -f "$RECORDER" "$RECORDER.body"
EXIT_CODE=1 GH_TOKEN="fake-token" GITHUB_REPOSITORY="o/r" RUNNER_TEMP="$tmp" \
  open_issue >/dev/null 2>&1
grep -qF 'TITLE=release-artifact-canary: could not evaluate the published artifact' "$RECORDER" ||
  note "a release finding must resolve the stale outage ledger (it proves the artifact evaluates)"

# ── 2b. an UNSET exit code (a cancelled step writes no output) is an outage ─────────
rm -f "$RECORDER" "$RECORDER.body"
EXIT_CODE="" GH_TOKEN="fake-token" GITHUB_REPOSITORY="o/r" RUNNER_TEMP="$tmp" open_issue >/dev/null 2>&1
grep -q 'could not evaluate' "$RECORDER" ||
  note "an empty exit code must not post a release-owed body"

# ── 3. the transcript TAIL, not the head ────────────────────────────────────────────
# 60 preamble lines, then the diagnosis: a head would show only the preamble.
{
  for i in $(seq 1 60); do echo "preamble line $i"; done
  echo "*** FATAL CONFIG FILE ERROR (Redis 8.10.2) ***"
  echo "[exit 1]"
} >"$tmp/canary/published-artifact-canary.log"
run_open 1 >/dev/null
grep -q 'FATAL CONFIG FILE ERROR' "$RECORDER.body" ||
  note "the body omits the failure — the transcript is read from the head, not the tail"
if grep -q 'preamble line 1$' "$RECORDER.body"; then
  note "the body carries the venv/pip preamble instead of the diagnosis"
fi

# ── 4. fail-closed when the alert cannot be filed ───────────────────────────────────
rm -f "$RECORDER"
if EXIT_CODE=1 GITHUB_REPOSITORY="o/r" RUNNER_TEMP="$tmp" open_issue >/dev/null 2>&1; then
  note "open with no GH_TOKEN exited 0 — a red run would look filed"
fi

# ── 5. close delegates to resolve, and is a no-op with no plumbing ──────────────────
rm -f "$RECORDER"
GH_TOKEN="fake" GITHUB_REPOSITORY="o/r" close_issues >/dev/null 2>&1
grep -q '^VERB=resolve$' "$RECORDER" || note "close did not delegate to the substrate's 'resolve' verb"
for ledger in \
  'TITLE=release-artifact-canary: published PyPI artifact is red' \
  'TITLE=release-artifact-canary: could not evaluate the published artifact'; do
  grep -qF "$ledger" "$RECORDER" ||
    note "close did not resolve the ledger: $ledger"
done
grep -q '^WHY=Green:' "$RECORDER" || note "close did not explain itself to the next reader"

if ! ( unset GH_TOKEN; GITHUB_REPOSITORY="o/r" close_issues >/dev/null 2>&1 ); then
  note "close with no plumbing exited non-zero — a green run must not be turned red"
fi

# ── 6. the dispatcher: an unknown verb is a usage error, not a silent no-op ─────────
# (executed, not sourced — this is the one place the harness runs the file as a script)
if GH_TOKEN=x GITHUB_REPOSITORY=o/r bash .github/scripts/published-artifact-alert.sh bogus \
  >/dev/null 2>&1; then
  note "an unknown verb exited 0"
fi

if [ "$fail" -ne 0 ]; then
  echo "published-artifact-alert harness: FAILED"
  exit 1
fi
echo "published-artifact-alert harness: PASS"
