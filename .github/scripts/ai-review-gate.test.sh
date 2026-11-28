#!/usr/bin/env bash
# ai-review-gate.test.sh — shell harness for the `ai-review-gate` workflow's
# signed-evidence validation logic (#2982).
#
# The gate lives inline in .github/workflows/ai-review-gate.yml (this workflow
# has no checkout step, so it cannot reference a repo script). To test its
# shell logic we EXTRACT the step's `run: |` block
# verbatim and execute it with a stubbed `gh` and a test HMAC key. Nothing is
# posted to GitHub and the real key is never read.
#
# Run: bash .github/scripts/ai-review-gate.test.sh
#
# Coverage (#2982):
#   (a) marker whose `@ sha` is STALE but whose signed `diff=` matches the
#       live PR diff                       → PASS (verdict carried forward)
#   (b) marker whose sha AND diff= both mismatch → FAIL
#   (c) legacy marker (no diff=) with sha == head → PASS
#   (d) `diff=` present but the live diff hash is UNAVAILABLE → FAIL CLOSED
#       for a STALE sha (d), while the sha-match path still works in that state
#       for a legacy marker (d2) AND for a HEAD-BOUND `diff=` marker (d3) —
#       whose head binding does not consult the live diff at all
#   (e) tampered `diff=` breaks the signature → FAIL
#   (f) unsigned marker → FAIL
#   (g) PR-body source: the REST fetch wins over the env var, falls back on
#       empty / `null` / a failed fetch, and a failed fetch never clobbers the
#       env-var body with gh's error envelope
#   (h) an EMPTY live diff is not hashed into a usable `sha256("")` value
#   (i) verdict=clean-micro is accepted on both acceptance paths
#   (j) marker selection: a wrong-diff or bad-signature candidate never masks
#       a following good one
#   (k) evidence is bound to this PR and this repo (other PR / other repo),
#       including a diff-bearing marker replayed from another PR with an
#       IDENTICAL diff= (the diff-match arm matches on the hash alone, so the
#       PR anchor is the only thing preventing the replay)
#   (l) a whitespace-padded GATE_SECRET is normalised
#   (m) a whitespace-ONLY GATE_SECRET fails closed (it normalizes to the EMPTY
#       public key — never a pass)
#   (n) a malformed trailing line cannot hijack the wrong-repo diagnostic and
#       suppress the accurate stale/diff explanation
#   (o) normalized-digest acceptance (#1362): a marker carrying the NORMALIZED
#       digest of a base-moved diff carries forward (the D1 win); the legacy
#       RAW digest is STILL accepted for the same diff (backward compat); a
#       marker matching neither is rejected; a normalized diff= still fails
#       closed when the fetch fails; and the head-bound path (rule (a)) is
#       unchanged
#   (p) a whitespace-only change changes the normalized digest (anti-
#       `patch-id` pin — `git patch-id --stable` would MATCH and falsely
#       carry a stale verdict forward)
#   (q) the #1362 binary carve-out (amendment to the 2026-09-23 ruling, per
#       the ruling's own rationale — agent-infra#1362 comment 5806797023):
#       two DIFFERENT binaries at the same path normalize to DIFFERENT
#       digests (the fail-open is closed, end to end at the gate), a text
#       base-move keeps the SAME normalized digest (no #4969 regression), a
#       binary base-move keeps it too, binary add/delete/mode-change entries
#       keep the `index` line, a hunk-less NON-binary entry keeps it as well
#       (the predicate is HUNK PRESENCE, not binary-marker presence), the
#       legacy raw-hash arm still accepts, and everything still fails closed
#       when the diff cannot be fetched
#   (q2) multi-entry ENTRY-SCOPING: two distinct binaries sharing a diff with a
#       hunk-bearing entry keep DISTINCT digests (binary before AND after the
#       hunk), and the multi-entry normalized bytes are pinned — a normalizer
#       that shares hunk state across entry boundaries, or drops an entry on
#       flush, fails HERE (every `index KEPT` vector above is single-entry)
#   plus direct vector tests of the extracted `normalize_review_diff`
#       function against the #1362 spec (ENTRY-SCOPED index-line drop —
#       dropped only when the entry carries a hunk, kept verbatim otherwise —
#       hunk-header rewrite with the absent-count default of 1, mode-width
#       boundary, and final-newline preservation)
#   (s) the #1224 evidence channels: the PR body AND its issue comments, with the
#       comment read's failure and provenance kept distinguishable from "no marker"
#   (i3) verdict=clean-low on both acceptance paths (#4755)
#   (t) acceptance rule (c) (#4255) — the PATCH projection survives a base advance:
#       pinned projection vectors (reviewed/live/edited/whitespace/renamed/deleted),
#       the anti-vacuity pairs, and (t1)-(t10)/(t20)-(t33), incl. (t10b), (t20b), (t29)-(t33) the end-to-end cases: the carry, the
#       three fail-closed arms, the probe cost bounds, the ANCHOR, the ANCESTRY and
#       authored-commit preconditions and their mutations, one HMAC verifier, the
#       relocation refusal, and every refusal's operator-facing diagnostic
#   (u) rule (c) END TO END ON REAL GIT BYTES: a real repo, a real base advance, a
#       real merge-forward — the reviewed record carries, one authored byte does not,
#       and a whitespace-only edit does not
#   (r) synthetic merge-queue batch (#5426): a `mergify[bot]`-authored PR on
#       `mergify/merge-queue/<sha>` passes even with no evidence (it is not a
#       review target and not a merge condition), while a NON-mergify author
#       with the same branch name, and a `mergify[bot]` PR on an ordinary
#       branch, both still FAIL — the guard cannot be bypassed by naming alone
#   plus the static invariants: the gate job must never gain
#   `if:`/`needs:`/`continue-on-error:` (any indentation or quoting), the
#   trigger must be EXACTLY `pull_request_target` (asserted over `pull_request*`
#   TOKENS, so `pull_request: {}`, a space before the colon, a quoted key, a
#   flow mapping or an inline `on: pull_request` cannot evade), the job must
#   carry no job-level `permissions:` override, the workflow must
#   grant `contents: read` + `pull-requests: read` and no `paths:` filter, the
#   step must declare `GH_TOKEN`, no unexpected `gh` call shape may occur, and
#   the extracted block must be the real gate step.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WF="$REPO_ROOT/.github/workflows/ai-review-gate.yml"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  ❌ $1"; }
assert_rc() {
    if [ "$GATE_RC" = "$1" ]; then ok "$2"; else bad "$2 (got rc=$GATE_RC want $1; out: $GATE_OUT)"; fi
}
assert_contains() {
    if grep -qF -- "$2" <<<"$GATE_OUT"; then ok "$1"; else bad "$1 (missing: $2; out: $GATE_OUT)"; fi
}
assert_not_contains() {
    if grep -qF -- "$2" <<<"$GATE_OUT"; then bad "$1 (unexpected: $2; out: $GATE_OUT)"; else ok "$1"; fi
}

T="$(mktemp -d /tmp/ai-review-gate-test.XXXXXX)"
trap 'rm -rf "$T"' EXIT

# ── Extract the gate's run block verbatim ────────────────────────────────
# `run: |` is at 8 spaces; content at 10. Blank lines belong to the block;
# the block ends at the first non-blank line indented less than 10.
RUN_BLOCK="$T/run-block.sh"
awk '
  /^        run: \|$/ { f=1; next }
  f {
    if ($0 ~ /^[[:space:]]*$/) { print ""; next }
    if ($0 ~ /^          /) { sub(/^          /, ""); print; next }
    exit
  }
' "$WF" > "$RUN_BLOCK"

if [ ! -s "$RUN_BLOCK" ]; then
    echo "  ❌ could not extract the run block from $WF"
    exit 1
fi
if ! grep -q 'AI_REVIEW_GATE_KEY' "$RUN_BLOCK" || ! grep -q 'live_diff_hash' "$RUN_BLOCK"; then
    echo "  ❌ extracted run block does not look like the ai-review-gate step (extraction is broken — fix the harness)"
    exit 1
fi
if bash -n "$RUN_BLOCK"; then ok "extracted run block parses (bash -n)"; else bad "extracted run block has a bash syntax error"; fi

# ── normalize_review_diff: direct vector tests (#1362) ────────────────────
# The gate normalizes the fetched diff before hashing it. Extract the helper
# from the SAME run block and drive the #1362 spec vectors through it directly,
# so a subtly wrong sed expression fails HERE with a named vector instead of
# only as an opaque digest mismatch in the end-to-end cases below.
NORM_FN="$T/normalize-fn.sh"
awk '/^normalize_review_diff\(\) \{/ { f=1 } f { print } f && /^\}$/ { exit }' \
    "$RUN_BLOCK" > "$NORM_FN"
if [ -s "$NORM_FN" ] && grep -q 'index \[0-9a-f\]' "$NORM_FN"; then
    ok "extracted normalize_review_diff from the run block"
    # shellcheck disable=SC1090
    source "$NORM_FN"
else
    bad "could not extract normalize_review_diff from the run block (the vector tests would pass vacuously)"
fi

check_norm() { # <input> <expected-output>
    local got
    got="$(printf '%s' "$1" | normalize_review_diff)"
    if [ "$got" = "$2" ]; then
        ok "normalize [$1] -> [$2]"
    else
        bad "normalize [$1] -> [$got] (want [$2])"
    fi
}
# Required #1362 vectors.
check_norm '@@ -1,5 +1,7 @@' '@@ -0,5 +0,7 @@'
check_norm '@@ -12 +12 @@' '@@ -0,1 +0,1 @@'
check_norm '@@ -12,0 +13,4 @@ def f():' '@@ -0,0 +0,4 @@ def f():'
# The index line is dropped ONLY inside an entry that carries a hunk (#1362
# amendment): the hunk content already carries the change.
check_norm $'diff --git a/x b/x\nindex 1a2b3c4..5d6e7f8 100644\n@@ -1,2 +1,3 @@\n ctx\n+added' \
           $'diff --git a/x b/x\n@@ -0,2 +0,3 @@\n ctx\n+added'
check_norm $'diff --git a/x b/x\nindex 1a2b3c4..5d6e7f8\n@@ -1 +1 @@\n-a\n+b' \
           $'diff --git a/x b/x\n@@ -0,1 +0,1 @@\n-a\n+b'
# ...and KEPT in a hunk-less entry, where no hunk content carries it. The
# predicate is HUNK PRESENCE, not the presence of a binary marker: a binary
# entry and a hunk-less empty-file add/delete BOTH keep the line.
check_norm 'index 1a2b3c4..5d6e7f8 100644' 'index 1a2b3c4..5d6e7f8 100644'
check_norm 'index 1a2b3c4..5d6e7f8' 'index 1a2b3c4..5d6e7f8'
check_norm $'diff --git a/f.bin b/f.bin\nindex 1111111..2222222 100644\nBinary files a/f.bin and b/f.bin differ' \
           $'diff --git a/f.bin b/f.bin\nindex 1111111..2222222 100644\nBinary files a/f.bin and b/f.bin differ'
check_norm $'diff --git a/empty b/empty\nnew file mode 100644\nindex 0000000..e69de29' \
           $'diff --git a/empty b/empty\nnew file mode 100644\nindex 0000000..e69de29'
# One side missing its count defaults that side to 1; the other count is kept.
check_norm '@@ -5,3 +9 @@ rest' '@@ -0,3 +0,1 @@ rest'
check_norm '@@ -5 +9,3 @@ rest' '@@ -0,1 +0,3 @@ rest'
# Mode width is EXACTLY six octal digits: seven digits is not git's index line.
check_norm 'index 1a2b3c4..5d6e7f8 1006440' 'index 1a2b3c4..5d6e7f8 1006440'
check_norm 'index 1a2b3c4..5d6e7f8 10064' 'index 1a2b3c4..5d6e7f8 10064'
# ...and the mode-width boundary holds INSIDE a hunk entry too: a 7-digit mode
# is not git's index line, so it is passed through even when a hunk is present.
check_norm $'diff --git a/x b/x\nindex 1a2b3c4..5d6e7f8 1006440\n@@ -1 +1 @@\n-a\n+b' \
           $'diff --git a/x b/x\nindex 1a2b3c4..5d6e7f8 1006440\n@@ -0,1 +0,1 @@\n-a\n+b'
# Non-hex and non-index lines pass through unchanged.
check_norm 'diff --git a/x b/x' 'diff --git a/x b/x'
# Final-newline state is preserved (sed); awk would append one and shift the
# digest, so this pins the choice of tool as much as the rewrite itself.
nl_missing="$(printf 'x' | normalize_review_diff | wc -c | tr -d ' ')"
nl_present="$(printf 'x\n' | normalize_review_diff | wc -c | tr -d ' ')"
if [ "$nl_missing" = "1" ] && [ "$nl_present" = "2" ]; then
    ok "normalization preserves the final-newline state (missing stays missing)"
else
    bad "normalization changed the final-newline state (missing -> '$nl_missing' bytes, present -> '$nl_present' bytes)"
fi

# ── (q2) multi-entry entry-scoping (the #1362 amendment, pinned) ──────────
# Every `index KEPT` vector above is a SINGLE-entry fixture, so a normalizer
# whose hunk state leaks ACROSS entry boundaries — a whole-diff `has_hunk`, or
# an entry reset that drops the preceding entry — satisfies them all. With a
# hunk-bearing entry sharing the diff, such a normalizer makes two DISTINCT
# binaries normalize IDENTICALLY: exactly the unreviewed-binary fail-open this
# amendment closes. These pairs differ ONLY in a binary entry's `index` line,
# in BOTH orders, and the multi-entry normalized BYTES are pinned verbatim.
HUNK_ENTRY=$'diff --git a/t.txt b/t.txt\nindex aaaaaaa..bbbbbbb 100644\n--- a/t.txt\n+++ b/t.txt\n@@ -1,3 +1,4 @@\n ctx\n+added\n ctx2'
BIN_V1_ENTRY=$'diff --git a/f.bin b/f.bin\nindex 1111111..2222222 100644\nBinary files a/f.bin and b/f.bin differ'
BIN_V2_ENTRY=$'diff --git a/f.bin b/f.bin\nindex 3333333..4444444 100644\nBinary files a/f.bin and b/f.bin differ'
# binary BEFORE hunk
printf '%s\n%s\n' "$BIN_V1_ENTRY" "$HUNK_ENTRY" > "$T/multi-bh-v1.diff"
printf '%s\n%s\n' "$BIN_V2_ENTRY" "$HUNK_ENTRY" > "$T/multi-bh-v2.diff"
# hunk BEFORE binary
printf '%s\n%s\n' "$HUNK_ENTRY" "$BIN_V1_ENTRY" > "$T/multi-hb-v1.diff"
printf '%s\n%s\n' "$HUNK_ENTRY" "$BIN_V2_ENTRY" > "$T/multi-hb-v2.diff"
for _order in bh hb; do
    _m1="$(normalize_review_diff < "$T/multi-${_order}-v1.diff" | openssl dgst -sha256 | awk '{print $NF}')"
    _m2="$(normalize_review_diff < "$T/multi-${_order}-v2.diff" | openssl dgst -sha256 | awk '{print $NF}')"
    if [ -n "$_m1" ] && [ "$_m1" != "$_m2" ]; then
        ok "(q2) distinct binaries keep DISTINCT digests in a multi-entry diff ($_order)"
    else
        bad "(q2) entry-scoping lost: distinct binaries normalized IDENTICALLY in a multi-entry diff ($_order) — $_m1"
    fi
done
# Pin the multi-entry bytes. Resetting the entry buffer BEFORE `flush` drops the
# binary ENTRY entirely; a whole-diff (precomputed) `has_hunk` drops the binary
# entry's kept `index` line. A STREAMING leak (initialise `has_hunk` once, never
# reset) keeps this `bh` vector green and is caught by the `hb` digest case
# above — which is why BOTH orders are asserted. This vector is what catches the
# entry-drop and precomputed variants, which would pass every single-entry
# assertion above.
check_norm "$(printf '%s\n%s\n' "$BIN_V1_ENTRY" "$HUNK_ENTRY")" \
           $'diff --git a/f.bin b/f.bin\nindex 1111111..2222222 100644\nBinary files a/f.bin and b/f.bin differ\ndiff --git a/t.txt b/t.txt\n--- a/t.txt\n+++ b/t.txt\n@@ -0,3 +0,4 @@\n ctx\n+added\n ctx2'

# ── Structural tripwires ─────────────────────────────────────────────────
# These invariants live in the YAML AROUND the extracted run block and so are
# unreachable by any runtime case below. Each is asserted statically, and each
# assertion is mutation-checked (see the comments). Isolate the blocks first.
# The anchors allow quoted keys and whitespace before the colon, because a
# quoted-but-identical key (`  "ai-review-gate":`) is the same job. An anchor
# that only matched the unquoted spelling extracted an EMPTY block for the
# others, and an empty block makes the tripwires below pass VACUOUSLY (they
# grep a 0-byte file). Fail closed on an empty extraction, like RUN_BLOCK.
awk '/^  ["'\'']?ai-review-gate["'\'']?[[:space:]]*:/ { f=1; next } f && /^  [^ ]/ { exit } f { print }' \
    "$WF" > "$T/job-block.yml"
# Print the `on:` line itself too: an inline `on: pull_request` is a valid
# spelling and its trigger token lives on that line.
awk '/^["'\'']?on["'\'']?[[:space:]]*:/ { f=1; print; next } f && /^[^ ]/ { exit } f { print }' \
    "$WF" > "$T/on-block.yml"
awk '/^["'\'']?permissions["'\'']?[[:space:]]*:/ { f=1; next } f && /^[^ ]/ { exit } f { print }' \
    "$WF" > "$T/perm-block.yml"
for _blk in job on perm; do
    if [ ! -s "$T/${_blk}-block.yml" ]; then
        echo "  ❌ could not extract the ${_blk} block from $WF — the structural tripwires would pass vacuously"
        exit 1
    fi
done

# (1) The gate job must never become conditional or non-blocking. A
#     SKIPPED check reports Success; `continue-on-error` swallows a failure.
#     Either one silently passes the check without evaluating any
#     evidence. The pattern matches the key as a TOKEN after an optional `?`
#     (YAML explicit key: `? if` / `: false` — valid Actions YAML that GitHub's
#     own parser accepts) or quote, and accepts a line that ENDS at the key,
#     because an explicit key puts its `:` on the NEXT line (cycle-3 review).
#     Text matching still cannot enumerate every spelling: the parsed-document
#     assertions in tests/test_ai_review_gate_contract.py are spelling-proof.
#     Plain, quoted (`    "if":`), space-padded (`    if :`) and explicit-key
#     (`    ? if`) forms are all matched.
if grep -qE '(^|[[:space:]{,?])["'\'']?(if|needs|continue-on-error)["'\'']?[[:space:]]*(:|$)' "$T/job-block.yml"; then
    bad "gate job gained if:/needs:/continue-on-error: — a skipped or swallowed check reports Success"
else
    ok "gate job carries no if:/needs:/continue-on-error: (must always run)"
fi

# (2) The trigger must be EXACTLY `pull_request_target`. Under `pull_request`, a
#     same-repo PR executes ITS OWN copy of this workflow (it can `exit 0` and
#     self-certify the check), and fork PRs stop receiving
#     AI_REVIEW_GATE_KEY. Assert the SET of `pull_request*` TOKENS in the `on:`
#     block, not a spelled-out key: a key-spelling matcher misses `pull_request
#     : {}` (space before colon), `{pull_request: {...}}` (flow mapping), a
#     quoted key, and an inline `on: pull_request` — each of which is the
#     banned trigger under a different, VALID spelling (review finding, cycle
#     2). Token-matching is spelling-agnostic; comments are stripped first so
#     prose cannot confuse it.
on_tokens="$(sed 's/#.*//' "$T/on-block.yml" | grep -oE 'pull_request[A-Za-z_-]*' | sort -u | sed '/^$/d' || true)"
if [ "$on_tokens" = "pull_request_target" ]; then
    ok "trigger is pull_request_target only (PR code can never run the gate)"
else
    bad "trigger is not exactly pull_request_target (pull_request* tokens found: $(printf '%s' "$on_tokens" | tr '\n' ',') ) — a same-repo PR could run its own gate definition"
fi

# (2b) The `edited` ACTIVITY TYPE must be present. Since #5433 this check is a
#      REQUIRED context and a queue ENTRY condition, and `record-review.sh` posts
#      the evidence marker by PATCHing the PR body — an `edited` event. That event
#      is the only thing that re-runs the gate after a review is recorded, so
#      dropping `edited` would leave every legitimately-reviewed PR red on its last
#      pre-record run, with nothing to re-trigger it. Token-matched (dependency-free
#      — this harness runs on a bare runner, no PyYAML) so `- edited`,
#      `[opened, edited]` and a nested list all read the same. Deliberately NOT an
#      assignment + `[ -n ]`: under `set -euo pipefail` a `grep` that finds nothing
#      makes the ASSIGNMENT exit non-zero, so `set -e` aborts the script and the
#      `bad` diagnostic below is dead in exactly the case it exists for (the abort
#      also skips the rest of the suite — 101 assertions ran silently skipped).
if sed 's/#.*//' "$T/on-block.yml" | grep -qE '\bedited\b'; then
    ok "trigger includes the 'edited' activity type (body edits re-run the gate)"
else
    bad "the pull_request_target trigger does not list 'edited' — record-review.sh posts the marker by editing the PR body, so without it a recorded review never re-runs the gate and every reviewed PR stays red on its pre-record run"
fi

# (3) No path filter may gate the workflow. A path-filtered workflow simply
#     does not RUN on a non-matching PR, so the gate silently stops reporting
#     — and a check that reports nothing is read as a pass (the local rail
#     refuses only on a RED). That is a silent bypass, not a deadlock.
#     Matched as a token, so a flow mapping on the trigger line and an
#     explicit key (`? paths`) are both caught.
if grep -qE '(^|[[:space:]{,?])["'\'']?(paths|paths-ignore)["'\'']?[[:space:]]*(:|$)' "$T/on-block.yml"; then
    bad "trigger gained a paths:/paths-ignore: filter — a path-filtered gate never runs, so it silently stops reporting (a skipped check reads as a pass to the rail)"
else
    ok "trigger carries no paths:/paths-ignore: filter"
fi

# (4) Reading a PR diff needs `pull-requests: read` (+ `contents: read` for the
#     repo). Without them `gh api` fails, live_diff_hash is never computed, and
#     the diff-match path is dead in production — silently restoring the #2982
#     deadlock while the GH_TOKEN assertion below still passes.
if grep -qE '^  contents:[[:space:]]*read[[:space:]]*$' "$T/perm-block.yml" \
   && grep -qE '^  pull-requests:[[:space:]]*read[[:space:]]*$' "$T/perm-block.yml"; then
    ok "permissions grant contents: read + pull-requests: read (gh api can read the diff)"
else
    bad "permissions do not grant contents: read + pull-requests: read — gh api cannot read the diff, so the diff-match path is dead code"
fi
# A JOB-level `permissions:` is valid YAML and OVERRIDES the workflow block for
# that job, so a job-level grant that drops `pull-requests: read` leaves the
# assertion above green while making the diff-match path dead in production.
if grep -qE '(^|[[:space:]{,?])["'\'']?permissions["'\'']?[[:space:]]*(:|$)' "$T/job-block.yml"; then
    bad "gate job carries a job-level permissions: override — it can drop pull-requests: read while the workflow-level grant still looks fine"
else
    ok "gate job carries no job-level permissions: override (workflow grant is effective)"
fi

# (5) Structural tripwire (#2982): the STEP must declare GH_TOKEN. `gh`
#     refuses to run in Actions without it, and the live diff hash depends on
#     `gh api` — with no token, `live_diff_hash` is permanently empty and the
#     entire diff-match path is dead code in production. The stubbed `gh`
#     below bypasses gh's auth check, so NO runtime case in this harness can
#     catch it; only this static assertion can. Not hypothetical: the first
#     revision of #2982 shipped without the token and every runtime case still
#     passed. The range is anchored at THIS step's name line: an `env:` block
#     on an earlier step must not be able to satisfy it.
if awk '/^      - name: Validate signed AI review evidence in PR body$/,/^        run: \|$/' "$WF" \
     | grep -qE '^          GH_TOKEN:'; then
    ok "gate step env declares GH_TOKEN (gh api is usable in Actions)"
else
    bad "gate step env declares NO GH_TOKEN — gh api fails in Actions, so live_diff_hash is always empty and the diff-match path is dead code in production"
fi

# ── Fixtures ─────────────────────────────────────────────────────────────
KEY="test-gate-key-2982"
REPO_NAME="daniel-ospina/tortoise"
PR_NUMBER="2982"
HEAD="$(printf 'a%.0s' $(seq 1 40))"    # current PR head
STALE="$(printf 'b%.0s' $(seq 1 40))"   # head the review was recorded at

DIFF_FILE="$T/diff.txt"
printf 'diff --git a/x b/x\n+hello\n' > "$DIFF_FILE"
DH="$(openssl dgst -sha256 < "$DIFF_FILE" | awk '{print $NF}')"
OTHER_DIFF_FILE="$T/diff2.txt"
printf 'diff --git a/x b/x\n+other\n' > "$OTHER_DIFF_FILE"
DH2="$(openssl dgst -sha256 < "$OTHER_DIFF_FILE" | awk '{print $NF}')"

# ── #1362 normalized-digest fixtures ──────────────────────────────────────
# A realistic diff whose `index` line and hunk headers move on a base update
# while every changed line stays byte-identical. The raw and normalized
# digests differ, so a marker keyed to one is refused by an unnormalized gate
# and accepted by this one. Both digests are pinned as LITERALS, independently
# computed, so a broken normalizer cannot pass by agreeing with itself.
DIFF_NORM_FILE="$T/diff-norm.txt"
cat > "$DIFF_NORM_FILE" <<'DIFFEOF'
diff --git a/x.py b/x.py
index 1111111..2222222 100644
--- a/x.py
+++ b/x.py
@@ -1,4 +1,6 @@
 ctx1
-old
+new
+added
 ctx2
@@ -20,3 +22,2 @@ def f():
 a
-b
+c
DIFFEOF
DH_RAW="a8d3ddc90a83487e02fe2d9af96b348dc113cd7b5dc80babcaf5d4bd2f2b213d"
DH_NORM_1362="efba06e08d0a4c1832f5ec0afdd8a142ce4f7b4b56777f1810f1657ecc22439e"
# Fixtures for the anti-`patch-id` pin: identical except for the whitespace in
# one changed line (`git patch-id --stable` ignores whitespace and MATCHES).
WS_A_FILE="$T/ws-a.txt"
WS_B_FILE="$T/ws-b.txt"
printf 'diff --git a/y b/y\n--- a/y\n+++ b/y\n@@ -1,1 +1,1 @@\n-hello\n+hello world\n' > "$WS_A_FILE"
printf 'diff --git a/y b/y\n--- a/y\n+++ b/y\n@@ -1,1 +1,1 @@\n-hello\n+hello  world\n' > "$WS_B_FILE"

sign() { printf '%s' "$1" | openssl dgst -sha256 -hmac "$KEY" | awk '{print $NF}'; }
legacy_marker() { # <sha>
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean @ $1 (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}
diff_marker() { # <sha> <diff-hash>
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean @ $1 diff=$2 (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}

# Stubbed gh: the gate asks for the PR diff, the PR body, and — since #1224 —
# the PR's issue comments (the second evidence channel). Every invocation is
# logged so that a production call-shape change (e.g. `--jq .body` →
# `--jq .title`) fails loudly instead of silently yielding empty output that
# the fallback then hides.
mkdir -p "$T/bin"
cat > "$T/bin/gh" <<'STUB'
#!/usr/bin/env bash
[ -n "${STUB_LOG:-}" ] && printf '%s\n' "$*" >> "$STUB_LOG"
# ── rule (c) (#4255): the reviewed side, reconstructed by the gate ────────
# TWO compare shapes, routed apart by the media type and the ref pair:
#   * `compare/<base>...<sha>` WITH the diff media type -> the reviewed DIFF bytes.
#   * `compare/<p_sha>...<head>` and `compare/<base>...<head>` WITHOUT it -> the
#     commit graph AS THE GATE'S OWN `--jq` WOULD RENDER IT (status, counts, a
#     "sha:parent-count" list).⛔ THE STUB CANNOT TELL A CHANGED `--jq` EXPRESSION
#     FROM A CHANGED FIXTURE — it renders the row itself, so swapping the gate's
#     expression for one that yields a SHORT row left this suite green (measured
#     2026-11-28, on a mutant that carries in production). What
#     catches that class is the GATE's own presence/arity guard in `patch_arm_ok`
#     — both counts must be present and numeric — and `(t32)` pins all three
#     directions of it plus a mutation control, and the two expression emulations
#     above redden every carry row the moment the gate's field set changes. Do not
#     read this stub as an assertion about jq itself — it cannot evaluate jq.
#
# The JSON arms come FIRST: a `compare/` request that reached the diff arm would be
# served diff bytes where the gate expects JSON (an unparseable answer => a
# fail-closed refusal), and every rule-(c) case would silently collapse to "red".
if grep -qE -- "/compare/" <<<"$*" && ! grep -qF -- "application/vnd.github.v3.diff" <<<"$*"; then
    if [ "${STUB_COMPARE_JSON_FAIL:-0}" = "1" ]; then exit 1; fi
    if grep -qE -- "compare/${STUB_BASE_SHA:-}\.\.\." <<<"$*"; then
        # ⛔ A PER-ARM failure switch. `STUB_COMPARE_JSON_FAIL` fails BOTH JSON compare
        # arms together, which makes "the branch-side read failed ALONE" inexpressible —
        # the guard at `[ -z "$p_bside" ]` was unpinned for exactly that reason (measured:
        # removing it, and its `:x` default, reddened no row at all).
        [ "${STUB_BRANCH_FAIL:-0}" = "1" ] && exit 1
        # ⛔ THE EXPRESSION IS PART OF THE CONTRACT (final review of #4255). The rows that
        # pin it are the ones that EXPECT A CARRY (`(t1)`, `(u3)`, `(t22)`, `(t23)`, `(t26)`,
        # `(t27)`, `(t28)`, …), not a row of their own: mutating either expression to one
        # that renders fewer fields reddens those rows instead of leaving the suite green.
        # ⛔ NO COUNTS ARE QUOTED HERE, DELIBERATELY. The first version pinned "29" and "4";
        # both were correct when written and had expired by the next commit, because later
        # rows and the branch-side guards change which fixtures reach the refusal. Re-measure
        # the mutant — never trust a number in a comment.
        # The gate reads this row by ARITY — a changed expression that renders fewer
        # fields makes it REFUSE (a safe over-block), but a stub that keeps rendering
        # the OLD field set would report a green suite while the gate has started
        # refusing every carry. So a call whose expression is not the pinned one is
        # served as a SHORT row — what such an expression actually yields — and the
        # rows that expect a carry redden until the stub and the gate agree again.
        # This is an EMULATION of the consequence, never a claim about jq itself.
        # ⛔ `.sha[0:12]` IS PART OF THE PINNED EXPRESSION (sign-off review, F3): the
        # membership test compares 12-char tokens, so if this half rendered 40-char shas
        # every token would MISS, an AUTHORED commit would read as "the base brought it
        # in", and the gate would carry it — measured, with the whole suite green.
        if ! grep -qF -- '[(.total_commits // 0), ([.commits[]?] | length), ([.commits[]? | .sha[0:12]]' <<<"$*"; then
            printf '%s\n' "${STUB_BRANCH_TOTAL:-0}"
            exit 0
        fi
        # The branch-side graph, post-`--jq`: <total_commits>\t<len(commits)>\t<s has>.
        # The middle field is the page length, so a case can reproduce a TRUNCATED
        # response (total > returned) and prove the gate refuses instead of treating a
        # missing sha as "came from the base". Default: a consistent page.
        # ⛔ THE COUNTS DEFAULT TO THE LIST'S OWN ARITY (review cycle 2). Before the
        # list-arity guard, a fixture could set only `STUB_BRANCH_SHAS` and leave the
        # counts at 0 — claiming one sha and rendering a list of one while the row said
        # `0\t0`. The gate tolerated it; the guard now refuses it, and a fixture must not
        # be able to create that inconsistency by OMISSION. Fixtures that deliberately
        # render a TRUNCATED page set BOTH explicitly (e.g. TOTAL=9 COUNT=1) and still do.
        # ⛔ A FIXTURE THAT SAYS NOTHING ABOUT THE GRAPH NOW RENDERS A PLAUSIBLE ONE
        # (review cycle 3). The gate requires a stale marker's page to be non-empty —
        # GitHub cannot render `ahead` with ZERO commits between two different shas —
        # so the previous silent default (`0` commits, empty list) described a page the
        # API cannot produce, and a fixture relying on it tested an impossible answer.
        # The default is now ONE base-brought MERGE commit (`ffffffffffff:2`),
        # which the branch-side list contains, i.e. a legal base advance.
        _bs_shas="${STUB_BRANCH_SHAS-ffffffffffff}"
        _bs_count="${STUB_BRANCH_COUNT:-$(printf '%s' "$_bs_shas" | wc -w | tr -d ' ')}"
        _bs_total="${STUB_BRANCH_TOTAL:-$_bs_count}"
        printf '%s\t%s\t%s\n' "$_bs_total" "$_bs_count" "$_bs_shas"
        exit 0
    fi
    # The reviewed→head graph, post-`--jq`: status, total, count, "sha:parents" list.
    # `STUB_ANCESTOR_ROW` serves a LITERAL row instead, which is how `(t32)` reproduces
    # a differently-shaped (short / absent / non-numeric) answer — the class a changed
    # `--jq` expression produces in production. An empty value means "use the switches".
    if [ -n "${STUB_ANCESTOR_ROW+set}" ]; then
        printf '%s\n' "${STUB_ANCESTOR_ROW}"
        exit 0
    fi
    # ⛔ THE EXPRESSION IS PART OF THE CONTRACT, for the reason spelled out
    # on the branch-side arm above: a changed expression must redden the rows that
    # expect a carry, not leave the suite green over an over-blocking gate.
    # ⛔ …and the same width pin on the REVIEWED arm (F3).
    if ! grep -qF -- '[.status, (.total_commits // 0), ([.commits[]?] | length), ([.commits[]? | (.sha[0:12]' <<<"$*"; then
        printf '%s\n' "${STUB_ANCESTOR_STATUS:-ahead}"
        exit 0
    fi
    # Same rule as the branch side above: the counts default to the LIST's arity, so a
    # fixture that sets only the commit list is internally consistent; a deliberately
    # truncated page still sets TOTAL/SEEN explicitly.
    _an_commits="${STUB_ANCESTOR_COMMITS-ffffffffffff:2}"
    _an_seen="${STUB_ANCESTOR_SEEN:-$(printf '%s' "$_an_commits" | wc -w | tr -d ' ')}"
    _an_total="${STUB_ANCESTOR_TOTAL:-$_an_seen}"
    printf '%s\t%s\t%s\t%s\n' "${STUB_ANCESTOR_STATUS:-ahead}" "$_an_total" \
        "$_an_seen" "$_an_commits"
    exit 0
fi
# Routed BEFORE the media-type arm: a `compare/<base>...<sha>` diff request
# carries the SAME media type as the live-diff request, so a stub that routed
# on the media type first would silently serve the LIVE diff as the REVIEWED
# diff — and the anchor would then be comparing the live bytes with their own
# digest, which is the one way rule (c) could pass vacuously.
if grep -qE -- "/compare/" <<<"$*"; then
    if [ "${STUB_COMPARE_FAIL:-0}" = "1" ]; then exit 1; fi
    cat "${STUB_COMPARE_FILE:?}"
    exit 0
fi
# The PR's BASE sha — read only by rule (c).
if grep -qF -- "--jq .base.sha" <<<"$*"; then
    if [ "${STUB_BASE_FAIL:-0}" = "1" ]; then
        printf '{"message":"Not Found","status":404}\n'
        exit 1
    fi
    printf '%s\n' "${STUB_BASE_SHA:?}"
    exit 0
fi
if grep -qF -- "application/vnd.github.v3.diff" <<<"$*"; then
    [ "${STUB_DIFF_FAIL:-0}" = "1" ] && exit 1
    cat "${STUB_DIFF_FILE:?}"
    exit 0
fi
# The PR's issue comments — the SECOND evidence channel (#1224). The default is
# a SUCCESSFUL fetch that returns nothing (no comment-carried evidence), which
# must stay distinguishable from the failure arm inside.
if grep -qE -- "/issues/[0-9]+/comments" <<<"$*"; then
    if [ "${STUB_COMMENTS_FAIL:-0}" = "1" ]; then
        # gh's real failure mode: the HTTP error envelope on STDOUT, rc != 0.
        printf '{"message":"Not Found","documentation_url":"https://docs.github.com/rest","status":404}\n'
        exit 1
    fi
    [ -n "${STUB_COMMENTS_FILE:-}" ] && cat "$STUB_COMMENTS_FILE"
    exit 0
fi
if grep -qF -- "--jq .body" <<<"$*"; then
    if [ "${STUB_BODY_FAIL:-0}" = "1" ]; then
        # gh's real failure mode: the HTTP error envelope on STDOUT, rc != 0.
        printf '{"message":"Not Found","documentation_url":"https://docs.github.com/rest","status":404}\n'
        exit 1
    fi
    cat "${STUB_BODY_FILE:?}"
    exit 0
fi
echo "stub gh: unrecognised invocation: $*" >&2
[ -n "${STUB_UNRECOGNISED:-}" ] && printf '%s\n' "$*" >> "$STUB_UNRECOGNISED"
exit 97
STUB
chmod +x "$T/bin/gh"

# Run the extracted gate. `<env-body-file>` seeds the event-payload PR_BODY;
# `<rest-body-file>` (default: the same file) is what the REST body fetch
# returns. Passing DISTINCT files is what proves which source the gate
# actually consumed — serving identical bytes to both (as this harness used to)
# makes the branch selection unobservable.
run_gate() { # <env-body-file> [<rest-body-file>]
    local envfile="$1" restfile="${2:-$1}" rcfile="$T/gate.rc" outfile="$T/gate.out"
    : > "$rcfile"; : > "$outfile"; : > "$T/gh.log"; : > "$T/gh-unrecognised"
    (
        export PATH="$T/bin:$PATH"
        export PR_BODY
        PR_BODY="$(cat "$envfile")"
        export HEAD_SHA="$HEAD" PR_NUMBER REPO_NAME
        # (#5426) Synthetic-queue-batch guard inputs. Defaults describe an
        # ordinary author PR; the (r) cases override them.
        export PR_AUTHOR="${PR_AUTHOR_OVERRIDE:-daniel-ospina}"
        export HEAD_REF="${HEAD_REF_OVERRIDE:-feat/author-branch}"
        export GATE_SECRET="${GATE_SECRET_OVERRIDE:-$KEY}"
        export STUB_BODY_FILE="$restfile" STUB_LOG="$T/gh.log"
        export STUB_UNRECOGNISED="$T/gh-unrecognised"
        # (#4255) rule (c) inputs. The defaults make every pre-existing case
        # behave exactly as before: no base sha and no compare fixture means the
        # patch arm refuses, which is what a pre-#4255 gate did by not existing.
        export STUB_BASE_SHA="${STUB_BASE_SHA:-}"
        export STUB_BASE_FAIL="${STUB_BASE_FAIL:-0}"
        export STUB_COMPARE_FILE="${STUB_COMPARE_FILE:-}"
        export STUB_COMPARE_FAIL="${STUB_COMPARE_FAIL:-0}"
        # (#1224) The comment channel. A per-case prefix sets these; the defaults
        # describe a successful fetch that found no comment evidence.
        export STUB_COMMENTS_FAIL="${STUB_COMMENTS_FAIL:-0}"
        export STUB_COMMENTS_FILE="${STUB_COMMENTS_FILE:-}"
        rc=0
        bash "$RUN_BLOCK" >"$outfile" 2>&1 || rc=$?
        printf '%s' "$rc" > "$rcfile"
    )
    GATE_RC="$(cat "$rcfile")"
    GATE_OUT="$(cat "$outfile")"
}

echo "── (a) stale sha, diff= matches live → carry forward ──────────"
diff_marker "$STALE" "$DH" > "$T/body-a"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-a"
assert_rc 0 "(a) gate passes"
assert_contains "(a) names the diff-match path" "passed via diff match"
# Call-shape drift: the gate must keep asking for the diff media type and the
# PR body in the exact shape the stub recognises. Without this, a production
# call-shape change yields empty output that the env-var fallback hides.
if grep -qF -- "application/vnd.github.v3.diff" "$T/gh.log"; then
    ok "(a) gate requested the diff media type"
else
    bad "(a) gate never requested the diff media type (stub log: $(cat "$T/gh.log"))"
fi
if grep -qF -- "--jq .body" "$T/gh.log"; then
    ok "(a) gate requested the PR body"
else
    bad "(a) gate never requested the PR body (stub log: $(cat "$T/gh.log"))"
fi
# Both the diff fetch and the body fetch must be scoped to THIS repo and THIS
# PR. If either ever drifts to a different PR, the diff-match arm would
# validate a marker against another PR's diff — the whole binding is void. The
# stub serves whatever it is given, so only the CALL SHAPE can catch this.
#
# #4801: SET MEMBERSHIP, not a count floor. The floor this used to be passed a
# call scoped to a FOREIGN repo as long as the two legitimate calls were also
# present — and the stub routes on the diff media type BEFORE it looks at the
# path, so a foreign diff fetch was "recognised", never reached
# `$T/gh-unrecognised`, and left both suites green. The gate hashes whatever
# lands in its temp file, so a foreign diff could be hashed while every drift
# net reported success.
#
# TWO properties, both required, neither implying the other:
#   SCOPE (first check) — no call may leave THIS repo and THIS PR.
#   SHAPE (second check) — a call that IS scoped to this PR's pulls endpoint
#     must be one of the two shapes the gate makes there (the diff fetch, or
#     the body fetch). Scope alone admits a same-scope call to a NEW endpoint
#     (`pulls/N/files`), and the stub would not flag it either because it
#     matches `--jq .body` — that is #4801's "fail on any THIRD shape" arm.
_gh_pulls_scope="repos/${REPO_NAME}/pulls/${PR_NUMBER}([^0-9]|$)"
_gh_comments_scope="repos/${REPO_NAME}/issues/${PR_NUMBER}/comments"
# (#4255) rule (c) reads the repo's COMPARE endpoint to reconstruct the diff at
# the marker's own signed `@ <sha>`. That endpoint is repo-scoped and carries no
# PR number, so it is admitted by REPO (never by a free-form path): a compare
# fetch aimed at another repository still lands off-scope below.
_gh_compare_scope="repos/${REPO_NAME}/compare/[0-9a-f.]+"
_gh_off_scope="$(grep -vE -- "${_gh_pulls_scope}|${_gh_comments_scope}|${_gh_compare_scope}" "$T/gh.log" || true)"
if [ -z "$_gh_off_scope" ]; then
    ok "(a) every gh call is scoped to repos/${REPO_NAME} for #${PR_NUMBER} (set membership)"
else
    bad "(a) gh call(s) outside this repo/PR: $(printf '%s' "$_gh_off_scope" | tr '\n' '|') (stub log: $(cat "$T/gh.log"))"
fi
# The two legitimate calls are the pulls RESOURCE itself — the diff fetch and
# the body fetch — so the shape net is a SUB-RESOURCE PROHIBITION, not a marker
# match. A marker net is blind to this: `pulls/N/files --jq .body` carries the
# same `--jq .body` marker as the legitimate body fetch, so it satisfied the
# old shape arm AND the count floor (`pulls/N/files` still matches the
# number-boundary pattern). Measured: the marker form left the `/files`
# mutation GREEN; this form is marker-blind and also catches sub-resources that
# carry no marker at all (`pulls/N/reviews`).
_gh_subres="repos/${REPO_NAME}/pulls/${PR_NUMBER}/"
_gh_bad_shape="$(grep -E -- "$_gh_subres" "$T/gh.log" || true)"
if [ -z "$_gh_bad_shape" ]; then
    ok "(a) no gh call reaches a pulls SUB-RESOURCE (only the resource itself)"
else
    bad "(a) gh call reached a pulls sub-resource: $(printf '%s' "$_gh_bad_shape" | tr '\n' '|')"
fi
# Kept ALONGSIDE the two checks above because it asserts a THIRD property:
# that BOTH the diff and the body fetch actually happened on this PR's pulls
# endpoint. Neither membership check implies it — the comment channel
# legitimately logs a non-pulls shape, so a gate that made only the diff call
# would still satisfy both.
if [ "$(grep -cE -- "repos/${REPO_NAME}/pulls/${PR_NUMBER}([^0-9]|$)" "$T/gh.log")" -ge 2 ]; then
    ok "(a) both gh calls target repos/${REPO_NAME}/pulls/${PR_NUMBER} (number-boundary exact)"
else
    bad "(a) a gh call is not scoped to repos/${REPO_NAME}/pulls/${PR_NUMBER} (stub log: $(cat "$T/gh.log"))"
fi
# The stub's diagnostic goes to STDERR, which the gate discards (`2>/dev/null`)
# on both calls — so grepping GATE_OUT for it can never fail. Assert against the
# stub's own side-channel file, so an UNEXPECTED gh call shape is detected
# instead of silently yielding empty output the env-var fallback then hides.
if [ ! -s "$T/gh-unrecognised" ]; then
    ok "(a) no unrecognised gh invocation"
else
    bad "(a) unrecognised gh invocation(s): $(cat "$T/gh-unrecognised")"
fi

echo "── (b) sha AND diff= both mismatch → fail ─────────────────────"
diff_marker "$STALE" "$DH2" > "$T/body-b"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-b"
assert_rc 1 "(b) gate fails"
assert_contains "(b) reports the diff divergence" "does not match this PR's current diff hash"
# legacy stale marker (no diff field at all) also fails, with the stale message.
legacy_marker "$STALE" > "$T/body-b2"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-b2"
assert_rc 1 "(b2) legacy stale marker fails"
assert_contains "(b2) reports stale evidence" "AI review evidence is stale"

echo "── (c) legacy marker, sha == head → pass (backward compat) ────"
legacy_marker "$HEAD" > "$T/body-c"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-c"
assert_rc 0 "(c) gate passes"
assert_contains "(c) normal pass message" "AI review gate passed:"
if grep -qF "passed via diff match" <<<"$GATE_OUT"; then
    bad "(c) legacy pass must not claim the diff path"
else
    ok "(c) legacy pass does not claim the diff path"
fi

echo "── (d) diff= present but live hash unavailable → fail closed ──"
diff_marker "$STALE" "$DH" > "$T/body-d"
STUB_DIFF_FILE="$DIFF_FILE" STUB_DIFF_FAIL=1 run_gate "$T/body-d"
assert_rc 1 "(d) gate fails closed"
assert_contains "(d) says the live hash was unavailable" "live diff hash could not be computed"
# ...but the sha-match path must remain usable when the API is down.
legacy_marker "$HEAD" > "$T/body-d2"
STUB_DIFF_FILE="$DIFF_FILE" STUB_DIFF_FAIL=1 run_gate "$T/body-d2"
assert_rc 0 "(d2) sha-match still passes without a live diff hash"
# d3: the same holds for a HEAD-BOUND `diff=` marker. Rule (a) matches on the
#     sha ALONE — the live diff hash is never consulted — so a diff= segment
#     cannot make rule (a) fail closed. The marker here carries DH2, a hash
#     that does NOT match the live diff: the pass can only come from the sha
#     binding, which is the intentional pre-#2982 behaviour. Pinned
#     deliberately: a "restore fail-closed" edit that gates rule (a) on
#     `[ -n "$live_diff_hash" ]` would break the normal path in production,
#     and must fail HERE instead.
diff_marker "$HEAD" "$DH2" > "$T/body-d3"
STUB_DIFF_FILE="$DIFF_FILE" STUB_DIFF_FAIL=1 run_gate "$T/body-d3"
assert_rc 0 "(d3) a head-bound diff= marker passes when the live diff fetch fails"
if grep -qF "passed via diff match" <<<"$GATE_OUT"; then
    bad "(d3) head-bound pass must not claim the diff path"
else
    ok "(d3) head-bound pass does not claim the diff path"
fi

echo "── (e) tampered diff= breaks the signature → fail ─────────────"
# Sign over diff=DH2, then swap in the live diff=DH without re-signing.
diff_marker "$STALE" "$DH2" | sed "s/diff=$DH2/diff=$DH/" > "$T/body-e"
if ! grep -qF "diff=$DH " "$T/body-e"; then
    bad "(e) fixture did not swap the diff= field"
else
    STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-e"
    assert_rc 1 "(e) gate fails"
    assert_contains "(e) reports an HMAC mismatch (#3076 wording)" "HMAC mismatch"
fi

echo "── (f) unsigned marker → never passes ─────────────────────────"
printf 'review recorded: reviews/%s.json verdict=clean @ %s (%s)\n' "$PR_NUMBER" "$HEAD" "$REPO_NAME" > "$T/body-f"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-f"
assert_rc 1 "(f) unsigned marker fails"
assert_contains "(f) explains it is not signed (#3076 wording)" "is UNSIGNED"

echo "── (g) PR-body source: REST fetch vs env-var fallback ─────────"
# g1: only the REST body carries the marker → the gate consumed the REST fetch.
diff_marker "$HEAD" "$DH" > "$T/body-g-rest"
printf 'no marker here\n' > "$T/body-g-env"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-g-env" "$T/body-g-rest"
assert_rc 0 "(g1) gate uses the REST-fetched body"
# g2: REST returns an empty body → fall back to the env var.
printf '' > "$T/body-g-empty"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-g-rest" "$T/body-g-empty"
assert_rc 0 "(g2) falls back to the env-var body when the REST body is empty"
# g3: REST returns the literal `null` → fall back to the env var.
printf 'null\n' > "$T/body-g-null"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-g-rest" "$T/body-g-null"
assert_rc 0 "(g3) falls back to the env-var body on a literal null"
# g4: REST fetch FAILS with gh's real error envelope on stdout and rc != 0 →
#     the gate must fall back, NOT adopt the error JSON as the body.
STUB_DIFF_FILE="$DIFF_FILE" STUB_BODY_FAIL=1 run_gate "$T/body-g-rest" "$T/body-g-rest"
assert_rc 0 "(g4) a failed REST fetch does not clobber the env-var body"
assert_contains "(g4) warns about the failed body fetch" "PR body fetch via the REST API failed"
assert_not_contains "(g4) does not misreport a failed fetch as missing evidence" "No AI review evidence found"
# g5: the REST body is authoritative when both are present and differ.
printf 'no marker here either\n' > "$T/body-g-rest-empty"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-g-rest" "$T/body-g-rest-empty"
assert_rc 1 "(g5) the REST body wins over the env-var body"

echo "── (h) empty live diff → fail closed, never sha256(\"\") ────────"
: > "$T/empty-diff.txt"
diff_marker "$STALE" "$DH" > "$T/body-h"
STUB_DIFF_FILE="$T/empty-diff.txt" run_gate "$T/body-h"
assert_rc 1 "(h) an empty live diff yields no usable hash"
assert_contains "(h) says the live hash was unavailable" "live diff hash could not be computed"
EMPTY_H="$(printf '' | openssl dgst -sha256 | awk '{print $NF}')"
diff_marker "$STALE" "$EMPTY_H" > "$T/body-h2"
STUB_DIFF_FILE="$T/empty-diff.txt" run_gate "$T/body-h2"
assert_rc 1 "(h2) sha256 of the empty string is never accepted as a live diff hash"

echo "── (i) verdict=clean-micro on both acceptance paths ───────────"
micro_sha_marker() {
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean-micro @ ${HEAD} (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}
micro_diff_marker() {
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean-micro @ ${STALE} diff=${DH} (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}
micro_sha_marker > "$T/body-i1"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-i1"
assert_rc 0 "(i1) clean-micro sha-match passes"
micro_diff_marker > "$T/body-i2"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-i2"
assert_rc 0 "(i2) clean-micro diff-match passes"

echo "── (i3) verdict=clean-low on both acceptance paths (#4755) ────"
clean_low_sha_marker() {
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean-low @ ${HEAD} (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}
clean_low_diff_marker() {
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean-low @ ${STALE} diff=${DH} (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}
clean_low_sha_marker > "$T/body-i3a"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-i3a"
assert_rc 0 "(i3a) clean-low sha-match passes"
clean_low_diff_marker > "$T/body-i3b"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-i3b"
assert_rc 0 "(i3b) clean-low diff-match passes"

# The vocabulary must stay CLOSED (#4755). A verdict OUTSIDE it is refused even
# with a valid HMAC over the marker text — the signature covers the verdict
# string, so a relabel verifies only if the recorder signed that relabel, and
# the regex must not admit it in the first place.
oov_sha_marker() {
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean-anything @ ${HEAD} (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}
oov_diff_marker() {
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean-anything @ ${STALE} diff=${DH} (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(sign "$m")"
}
oov_sha_marker > "$T/body-i3c"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-i3c"
assert_rc 1 "(i3c) an out-of-vocabulary verdict is refused at the head"
oov_diff_marker > "$T/body-i3d"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-i3d"
assert_rc 1 "(i3d) an out-of-vocabulary verdict is refused on the diff-match path"

echo "── (j) marker selection: a bad candidate never masks a good one ─"
# j1: a correctly-signed marker for the WRONG diff precedes the good one.
{ diff_marker "$STALE" "$DH2"; diff_marker "$STALE" "$DH"; } > "$T/body-j1"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-j1"
assert_rc 0 "(j1) skips a wrong-diff candidate and accepts the matching one"
assert_contains "(j1) accepted the matching marker" "diff=$DH ("
# j2: a bad-signature candidate precedes the good one.
{
    printf 'review recorded: reviews/%s.json verdict=clean @ %s diff=%s (%s) sig=%s\n' \
        "$PR_NUMBER" "$STALE" "$DH" "$REPO_NAME" "$(printf 'f%.0s' $(seq 1 64))"
    diff_marker "$STALE" "$DH"
} > "$T/body-j2"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-j2"
assert_rc 0 "(j2) skips a bad-signature candidate and accepts the next valid one"

echo "── (k) evidence is bound to this PR and this repo ─────────────"
# k1: a validly-signed marker for ANOTHER PR cannot satisfy this one.
other_pr_m="review recorded: reviews/9999.json verdict=clean @ ${HEAD} (${REPO_NAME})"
printf '%s sig=%s\n' "$other_pr_m" "$(sign "$other_pr_m")" > "$T/body-k1"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-k1"
assert_rc 1 "(k1) a marker recorded for another PR is rejected"
assert_contains "(k1) reports no evidence for this PR" "No AI review evidence found in PR #${PR_NUMBER}"
# k2: a validly-signed marker for ANOTHER REPO is rejected, and the diagnostic
#     names the repo instead of blaming the diff (two identical hashes).
other_repo_m="review recorded: reviews/${PR_NUMBER}.json verdict=clean @ ${STALE} diff=${DH} (other-org/other-repo)"
printf '%s sig=%s\n' "$other_repo_m" "$(sign "$other_repo_m")" > "$T/body-k2"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-k2"
assert_rc 1 "(k2) a marker recorded for another repo is rejected"
assert_contains "(k2) names the recorded repo" "was found for other-org/other-repo"
# k3: PR-anchoring of the diff-match arm. A validly-signed marker for ANOTHER PR
#     whose signed diff= EQUALS this PR's live diff must not be replayable here.
#     The diff-match arm matches on the hash alone, so without the
#     `reviews/<PR_NUMBER>.json` anchor an identical diff on an unrelated PR
#     (e.g. a reverted/cherry-picked change) would cross-satisfy this one.
other_pr_diff_m="review recorded: reviews/9999.json verdict=clean @ ${STALE} diff=${DH} (${REPO_NAME})"
printf '%s sig=%s\n' "$other_pr_diff_m" "$(sign "$other_pr_diff_m")" > "$T/body-k3"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-k3"
assert_rc 1 "(k3) a diff-bearing marker recorded for another PR is rejected"
assert_contains "(k3) reports no evidence for this PR" "No AI review evidence found in PR #${PR_NUMBER}"

echo "── (l) a whitespace-padded GATE_SECRET is normalised ─────────"
# The deployed key file is 64 hex chars PLUS a trailing newline; the gate
# strips whitespace before HMACing. Lose that line and every signature breaks,
# while every whitespace-free fixture above still passes.
diff_marker "$HEAD" "$DH" > "$T/body-l"
GATE_SECRET_OVERRIDE=$'\n  '"$KEY"$'  \n'
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-l"
unset GATE_SECRET_OVERRIDE
assert_rc 0 "(l) a whitespace-padded key is normalised"

echo "── (m) a whitespace-ONLY key fails closed ─────────────────────"
# A whitespace-only secret passes the raw `-z` guard and then normalizes to the
# EMPTY string, which is a PUBLIC HMAC key: any PR author could mint a
# stale-sha marker carrying the live `diff=` and turn the check green
# on an unreviewed diff. The gate must re-validate AFTER normalization.
# Sign the marker with the EMPTY key — the key every attacker knows once the
# secret normalizes to "". If the gate skips validation of the NORMALIZED key,
# this marker verifies and the check passes on an unreviewed diff.
empty_key_marker() {
    local m="review recorded: reviews/${PR_NUMBER}.json verdict=clean @ ${HEAD} diff=${DH} (${REPO_NAME})"
    printf '%s sig=%s\n' "$m" "$(printf '%s' "$m" | openssl dgst -sha256 -hmac "" | awk '{print $NF}')"
}
empty_key_marker > "$T/body-m"
GATE_SECRET_OVERRIDE=$'\n \t \n'
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-m"
unset GATE_SECRET_OVERRIDE
assert_rc 1 "(m) a whitespace-only key never becomes the empty HMAC key"
assert_contains "(m) names the normalization failure" "normalises to an empty value"

echo "── (n) a malformed trailing line cannot hijack the repo diagnostic ─"
# A well-formed but STALE own-repo marker followed by a line that carries a
# trailing ` (other/repo) sig=<hex>` but no well-formed 40-hex sha must still
# report the real cause (stale). Naming `other/repo` suppresses the accurate
# stale/diff diagnostic on a red check.
{
    diff_marker "$STALE" "$DH2"
    printf 'review recorded: reviews/%s.json verdict=clean @ not-a-sha (some-other/place) sig=%s\n' \
        "$PR_NUMBER" "$(printf 'f%.0s' $(seq 1 64))"
} > "$T/body-n"
STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-n"
assert_rc 1 "(n) gate fails"
# The stale verdict must name the condition it actually established (#4776): no marker in
# the body is bound to the head, and the marker that IS there is listed as a candidate. The
# previous assertion pinned `latest recorded ${STALE} — expected ${HEAD}`, a label that
# overclaimed (it is the LAST sha-bearing line, not the newest record) and that a reader
# could not re-check once the PR moved on.
assert_contains "(n) reports the real cause" "NO marker in this PR's body or its admitted comments is bound to ${HEAD}"
assert_contains "(n) says which bytes it judged" "comments=read"
assert_contains "(n) lists the candidate marker it did see" "bound to: ${STALE:0:12}"
assert_contains "(n) keeps the head in the verdict" "head expected: ${HEAD}"
assert_not_contains "(n) does not misattribute the repo" "was found for some-other/place"

echo "── (o) normalized diff digest (#1362) ─────────────────────────"
# o1 — the D1 win. Post-#1362 the producer signs the NORMALIZED digest, so a
# base move that rewrote only index/hunk headers still carries the verdict
# forward. The marker's sha is stale, so acceptance can ONLY come from (b).
diff_marker "$STALE" "$DH_NORM_1362" > "$T/body-o1"
STUB_DIFF_FILE="$DIFF_NORM_FILE" run_gate "$T/body-o1"
assert_rc 0 "(o1) a normalized-hash marker carries forward"
assert_contains "(o1) names the diff-match path" "passed via diff match"
assert_contains "(o1) reports the normalized digest matched" "matched the normalized digest"
# o2 — backward compatibility. The legacy RAW digest is STILL accepted for the
# same diff. Without this arm every marker already recorded breaks and the
# check reddens fleet-wide. This is the case that pins the consumer-
# first land order.
diff_marker "$STALE" "$DH_RAW" > "$T/body-o2"
STUB_DIFF_FILE="$DIFF_NORM_FILE" run_gate "$T/body-o2"
assert_rc 0 "(o2) a legacy raw-hash marker is still accepted (backward compat)"
assert_contains "(o2) reports the raw digest matched" "matched the raw digest"
# o3 — a marker matching NEITHER digest is rejected, and the message names
# BOTH live digests (normalized + raw) so the operator is not left guessing.
DH_NEITHER="$(printf 'deadbeef%.0s' $(seq 1 8))"   # exactly 64 hex chars
diff_marker "$STALE" "$DH_NEITHER" > "$T/body-o3"
STUB_DIFF_FILE="$DIFF_NORM_FILE" run_gate "$T/body-o3"
assert_rc 1 "(o3) a marker matching neither digest is rejected"
assert_contains "(o3) reports the diff divergence" "does not match this PR's current diff hash"
assert_contains "(o3) names the normalized digest" "normalized="
assert_contains "(o3) names the raw digest" "raw="
# o4 — fail closed. When the diff cannot be fetched BOTH digests are empty and
# a diff= marker is not accepted via (b). A transient API failure is not a
# bypass, exactly as before normalization.
diff_marker "$STALE" "$DH_NORM_1362" > "$T/body-o4"
STUB_DIFF_FILE="$DIFF_NORM_FILE" STUB_DIFF_FAIL=1 run_gate "$T/body-o4"
assert_rc 1 "(o4) normalized diff= fails closed when the fetch fails"
assert_contains "(o4) says the live hash was unavailable" "live diff hash could not be computed"
# o5 — rule (a) is unchanged. A diff= marker at the CURRENT head passes on the
# sha binding alone even when the fetch fails and its diff= matches neither
# digest. Normalization must never gate the head-bound path.
diff_marker "$HEAD" "$DH_NEITHER" > "$T/body-o5"
STUB_DIFF_FILE="$DIFF_NORM_FILE" STUB_DIFF_FAIL=1 run_gate "$T/body-o5"
assert_rc 0 "(o5) the head-bound path is unchanged by normalization"
if grep -qF "passed via diff match" <<<"$GATE_OUT"; then
    bad "(o5) head-bound pass must not claim the diff path"
else
    ok "(o5) head-bound pass does not claim the diff path"
fi

echo "── (p) whitespace-only change changes the digest (anti-patch-id) ─"
# `git patch-id --stable` and its default IGNORE whitespace, so it would MATCH
# these two diffs and carry a stale verdict across a real whitespace-only
# change — a false accept. sha256 over normalized bytes must NOT. This is the
# reason the gate does not use patch-id, pinned as an executable property.
DH_WS_A="$(normalize_review_diff < "$WS_A_FILE" | openssl dgst -sha256 | awk '{print $NF}')"
DH_WS_B="$(normalize_review_diff < "$WS_B_FILE" | openssl dgst -sha256 | awk '{print $NF}')"
if [ -n "$DH_WS_A" ] && [ "$DH_WS_A" != "$DH_WS_B" ]; then
    ok "(p) a whitespace-only change yields a different normalized digest"
else
    bad "(p) a whitespace-only change did NOT change the digest — a patch-id-style normalizer would carry a stale verdict forward (got '$DH_WS_A' vs '$DH_WS_B')"
fi
# ...and end to end: a marker for the pre-whitespace diff is refused against the
# post-whitespace live diff.
diff_marker "$STALE" "$DH_WS_A" > "$T/body-p"
STUB_DIFF_FILE="$WS_B_FILE" run_gate "$T/body-p"
assert_rc 1 "(p) a marker for the pre-whitespace diff is rejected"

echo "── (q) #1362 binary carve-out: entry-scoped index retention ────"
# The amendment to the 2026-09-23 ruling (recorded on agent-infra#1362, comment
# 5806797023, per the ruling's OWN rationale): the `index` line is dropped
# exactly when the hunk content already carries the change. A binary entry has
# no hunks and `Binary files … differ` carries no content, so an unconditional
# drop made two DISTINCT binary revisions normalize identically — review v1,
# sign, swap in v2, and the gate ACCEPTED the unreviewed binary.

# (a) The fail-open is CLOSED. Two different binaries at the same path must
#     produce DIFFERENT normalized digests. This is the mutation-pinned case:
#     restore the unconditional index drop and BOTH entries normalize to the
#     same bytes, so this assertion goes red (verified by mutating the
#     normalizer to always drop — see the PR body).
BIN1="$T/bin1.diff"; BIN2="$T/bin2.diff"
printf 'diff --git a/f.bin b/f.bin\nindex 1111111..2222222 100644\nBinary files a/f.bin and b/f.bin differ\n' > "$BIN1"
printf 'diff --git a/f.bin b/f.bin\nindex 3333333..4444444 100644\nBinary files a/f.bin and b/f.bin differ\n' > "$BIN2"
DH_BIN1_NORM="$(normalize_review_diff < "$BIN1" | openssl dgst -sha256 | awk '{print $NF}')"
DH_BIN2_NORM="$(normalize_review_diff < "$BIN2" | openssl dgst -sha256 | awk '{print $NF}')"
if [ -n "$DH_BIN1_NORM" ] && [ "$DH_BIN1_NORM" != "$DH_BIN2_NORM" ]; then
    ok "(a) two different binaries normalize to DIFFERENT digests"
else
    bad "(a) two different binaries normalized IDENTICALLY ($DH_BIN1_NORM) — the index-line drop fail-open is back"
fi
# ...and end to end at the GATE, which is where the fail-open was exploitable:
# a marker signed for the reviewed v1 is accepted against v1 and REJECTED
# against the unreviewed v2.
diff_marker "$STALE" "$DH_BIN1_NORM" > "$T/body-bin1"
STUB_DIFF_FILE="$BIN1" run_gate "$T/body-bin1"
assert_rc 0 "(a) a marker for the reviewed binary v1 is accepted against v1"
assert_contains "(a) names the diff-match path" "passed via diff match"
STUB_DIFF_FILE="$BIN2" run_gate "$T/body-bin1"
assert_rc 1 "(a) the SAME marker is REJECTED against the unreviewed binary v2 (fail-open closed)"
assert_contains "(a) reports the diff divergence for v2" "does not match this PR's current diff hash"

# (b) Text base-move keeps the SAME normalized digest — #4969's win is not
#     regressed. The two renderings differ only in the `index` line and the
#     hunk start lines; every content line is identical.
TEXT_B1="$T/text-b1.diff"; TEXT_B2="$T/text-b2.diff"
printf 'diff --git a/f b/f\nindex 1111111..2222222 100644\n--- a/f\n+++ b/f\n@@ -1,3 +1,4 @@\n ctx\n+added\n ctx2\n' > "$TEXT_B1"
printf 'diff --git a/f b/f\nindex aaaaaaa..bbbbbbb 100644\n--- a/f\n+++ b/f\n@@ -10,3 +11,4 @@\n ctx\n+added\n ctx2\n' > "$TEXT_B2"
DH_TB1="$(normalize_review_diff < "$TEXT_B1" | openssl dgst -sha256 | awk '{print $NF}')"
DH_TB2="$(normalize_review_diff < "$TEXT_B2" | openssl dgst -sha256 | awk '{print $NF}')"
if [ "$DH_TB1" = "$DH_TB2" ]; then
    ok "(b) a text base-move leaves the normalized digest unchanged"
else
    bad "(b) a text base-move changed the normalized digest — #4969's win regressed ($DH_TB1 vs $DH_TB2)"
fi
RAW_TB1="$(openssl dgst -sha256 < "$TEXT_B1" | awk '{print $NF}')"
RAW_TB2="$(openssl dgst -sha256 < "$TEXT_B2" | awk '{print $NF}')"
if [ "$RAW_TB1" != "$RAW_TB2" ]; then
    ok "(b) control: the RAW digest did move (so this is the normalization, not the fixture)"
else
    bad "(b) control: the raw digest did NOT move — the fixture is not a base-move shape"
fi

# (c) Binary base-move keeps the SAME normalized digest. A no-regression
#     guard (not a mutation pin — (a)/(d)/(e) carry the pins): the base move
#     rewrites the sibling TEXT entry's index/header; the binary entry is
#     byte-identical (a base move does not touch the binary's content, so its
#     blob-OID pair does not move), so the KEPT index line cannot make the
#     digest move either.
BIN_B1="$T/binb1.diff"; BIN_B2="$T/binb2.diff"
printf 'diff --git a/f.bin b/f.bin\nindex 1111111..2222222 100644\nBinary files a/f.bin and b/f.bin differ\ndiff --git a/t.txt b/t.txt\nindex 1111111..2222222 100644\n--- a/t.txt\n+++ b/t.txt\n@@ -1,3 +1,4 @@\n ctx\n+added\n ctx2\n' > "$BIN_B1"
printf 'diff --git a/f.bin b/f.bin\nindex 1111111..2222222 100644\nBinary files a/f.bin and b/f.bin differ\ndiff --git a/t.txt b/t.txt\nindex aaaaaaa..bbbbbbb 100644\n--- a/t.txt\n+++ b/t.txt\n@@ -10,3 +11,4 @@\n ctx\n+added\n ctx2\n' > "$BIN_B2"
DH_BB1="$(normalize_review_diff < "$BIN_B1" | openssl dgst -sha256 | awk '{print $NF}')"
DH_BB2="$(normalize_review_diff < "$BIN_B2" | openssl dgst -sha256 | awk '{print $NF}')"
if [ "$DH_BB1" = "$DH_BB2" ]; then
    ok "(c) a binary base-move leaves the normalized digest unchanged"
else
    bad "(c) a binary base-move changed the normalized digest ($DH_BB1 vs $DH_BB2)"
fi

# (d) Binary add / delete / binary+mode-change entries keep the `index` line.
BIN_ADD="$T/bin-add.diff"; BIN_DEL="$T/bin-del.diff"; BIN_MODE="$T/bin-mode.diff"
printf 'diff --git a/f.bin b/f.bin\nnew file mode 100644\nindex 0000000..2222222\nBinary files /dev/null and b/f.bin differ\n' > "$BIN_ADD"
printf 'diff --git a/f.bin b/f.bin\ndeleted file mode 100644\nindex 1111111..0000000\nBinary files a/f.bin and /dev/null differ\n' > "$BIN_DEL"
printf 'diff --git a/f.bin b/f.bin\nold mode 100644\nnew mode 100755\nindex 1111111..2222222\nBinary files a/f.bin and b/f.bin differ\n' > "$BIN_MODE"
for _spec in "add:$BIN_ADD:index 0000000\.\.2222222" "delete:$BIN_DEL:index 1111111\.\.0000000" "mode:$BIN_MODE:index 1111111\.\.2222222"; do
    _name="${_spec%%:*}"; _rest="${_spec#*:}"; _file="${_rest%%:*}"; _re="${_rest#*:}"
    if normalize_review_diff < "$_file" | grep -qE "^${_re}$"; then
        ok "(d) binary ${_name} entry keeps its index line"
    else
        bad "(d) binary ${_name} entry LOST its index line"
    fi
done

# (e) A hunk-less NON-binary entry keeps the `index` line. The predicate is
#     HUNK PRESENCE, not the presence of a binary marker: an empty-file add
#     (`index 0000000..e69de29`) has no hunk and no `Binary files` line, and
#     must still keep the line.
printf 'diff --git a/empty b/empty\nnew file mode 100644\nindex 0000000..e69de29\n' > "$T/empty-add.diff"
if normalize_review_diff < "$T/empty-add.diff" | grep -qE '^index 0000000\.\.e69de29$'; then
    ok "(e) a hunk-less non-binary (empty-file add) entry keeps its index line"
else
    bad "(e) a hunk-less non-binary entry LOST its index line — the predicate is binary-marker presence, not hunk presence"
fi
# ...and the flip side, so (e) cannot pass vacuously: the SAME index line IS
# dropped once the entry carries a hunk.
printf 'diff --git a/empty b/empty\nindex 0000000..e69de29\n@@ -0,0 +1 @@\n+x\n' > "$T/empty-hunk.diff"
if normalize_review_diff < "$T/empty-hunk.diff" | grep -qE '^index '; then
    bad "(e) the index line survived a HUNK-bearing entry — the drop is not entry-scoped"
else
    ok "(e) the same index line IS dropped when the entry carries a hunk"
fi

# (f) The legacy RAW-hash arm still accepts (unchanged behaviour). A marker
#     signed with the raw digest of a hunk-bearing diff is accepted, so every
#     marker already recorded stays valid (the consumer-first land order).
#     A hunk-bearing fixture is required: for a binary-only entry the carve-out
#     keeps the index line, so the normalized bytes EQUAL the raw bytes and the
#     raw arm is not separately observable.
DH_TB1_RAW="$(openssl dgst -sha256 < "$TEXT_B1" | awk '{print $NF}')"
DH_TB1_NORM="$(normalize_review_diff < "$TEXT_B1" | openssl dgst -sha256 | awk '{print $NF}')"
if [ "$DH_TB1_RAW" != "$DH_TB1_NORM" ]; then
    ok "(f) fixture: the raw and normalized digests differ (so the arm is observable)"
else
    bad "(f) fixture: raw == normalized, so the legacy arm is not exercised"
fi
diff_marker "$STALE" "$DH_TB1_RAW" > "$T/body-raw-1362"
STUB_DIFF_FILE="$TEXT_B1" run_gate "$T/body-raw-1362"
assert_rc 0 "(f) a legacy raw-hash marker is still accepted (backward compat)"
assert_contains "(f) reports the raw digest matched" "matched the raw digest"

# (g) Everything still fails closed when the diff cannot be fetched: BOTH
#     digests are empty, so a stale-sha binary `diff=` marker is not accepted.
diff_marker "$STALE" "$DH_BIN1_NORM" > "$T/body-bin-fc"
STUB_DIFF_FILE="$BIN1" STUB_DIFF_FAIL=1 run_gate "$T/body-bin-fc"
assert_rc 1 "(g) a binary normalized diff= fails closed when the diff fetch fails"
assert_contains "(g) says the live hash was unavailable" "live diff hash could not be computed"

# (g2) A FAILING normalizer clears BOTH digests, not just the normalized one.
#      The guard at the fetch site only covers a MISSING python3; this exercises
#      the else-branch that a present-but-broken normalizer takes. Without it, a
#      broken normalizer would silently fall back to the raw arm and a stale
#      raw `diff=` marker would still be accepted.
cat > "$T/bin/python3" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
chmod +x "$T/bin/python3"
diff_marker "$STALE" "$DH_BIN1_NORM" > "$T/body-normfail"
STUB_DIFF_FILE="$BIN1" run_gate "$T/body-normfail"
assert_rc 1 "(g2) a failing normalizer rejects a normalized diff= marker (fail closed)"
assert_contains "(g2) says the live hash was unavailable" "live diff hash could not be computed"
# ...and the legacy RAW arm is cleared too (no silent fallback to the weaker arm).
diff_marker "$STALE" "$DH_TB1_RAW" > "$T/body-raw-normfail"
STUB_DIFF_FILE="$TEXT_B1" run_gate "$T/body-raw-normfail"
assert_rc 1 "(g2) a failing normalizer clears the RAW arm too (no silent fallback)"
rm -f "$T/bin/python3"

# (r) Synthetic merge-queue batch guard (#5426). Mergify's queue PR is authored
#     by `mergify[bot]` on `mergify/merge-queue/<sha>` and can never carry an
#     attestation, so the gate must pass it explicitly instead of reddening
#     every batch. The guard needs BOTH signals: naming alone must not bypass
#     the author-PR gate.
printf 'merge queue: checking #1 + #2 together\n' > "$T/body-r-queue"
PR_AUTHOR_OVERRIDE="mergify[bot]" HEAD_REF_OVERRIDE="mergify/merge-queue/19ba552894" \
    STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-r-queue"
assert_rc 0 "(r1) a mergify[bot] queue batch passes with no evidence"
assert_contains "(r1) explains why (not a review target)" "Synthetic merge-queue batch"
# (r2) Bypass control: a contributor branch named mergify/merge-queue/... is NOT
#      authored by the bot and must still fail closed.
printf 'no marker\n' > "$T/body-r-bypass"
PR_AUTHOR_OVERRIDE="daniel-ospina" HEAD_REF_OVERRIDE="mergify/merge-queue/19ba552894" \
    STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-r-bypass"
assert_rc 1 "(r2) a non-mergify author with a queue-shaped branch still fails closed"
assert_contains "(r2) reports no evidence" "No AI review evidence found"
# (r3) The other signal alone must not bypass either: a mergify[bot] PR on an
#      ordinary branch is an author PR and is evaluated normally.
printf 'no marker\n' > "$T/body-r-botbranch"
PR_AUTHOR_OVERRIDE="mergify[bot]" HEAD_REF_OVERRIDE="feat/author-branch" \
    STUB_DIFF_FILE="$DIFF_FILE" run_gate "$T/body-r-botbranch"
assert_rc 1 "(r3) mergify[bot] on an ordinary branch is still evaluated (fails with no evidence)"

echo "── (s) the comment channel (#1224) ────────────────────────────"
# The marker may be carried by a repo-affiliated PR COMMENT instead of the body:
# the body is a mutable field any later legitimate edit rewrites, so a body-only
# copy reddens this check on a review that genuinely happened. This pins that the
# gate PASSES on comment evidence when the body holds none, and that a comment
# fetch failure still fails closed. (A call-shape drift is caught by case (a)'s
# $T/gh-unrecognised check, which every case feeds, because the comment fetch is
# unconditional.)
printf 'edited body — the marker was removed by the last rewrite\n' > "$T/body-s"
legacy_marker "$HEAD" > "$T/comments-s"
STUB_DIFF_FILE="$DIFF_FILE" STUB_COMMENTS_FILE="$T/comments-s" run_gate "$T/body-s"
assert_rc 0 "(s) gate passes on a comment-carried marker alone"
assert_contains "(s) names the channel it used" "marker found in PR comment"
STUB_DIFF_FILE="$DIFF_FILE" STUB_COMMENTS_FAIL=1 run_gate "$T/body-s"
assert_rc 1 "(s) a failed comment fetch fails closed"
assert_contains "(s) says the comment evidence could not be examined" "comment-carried evidence could not be examined"

echo ""
echo "── (t) acceptance rule (c): the PATCH projection survives a base advance (#4255) ──"
# THE WALL THIS SECTION GUARDS. `main` landing work in a file the PR also touches rewrites
# the hunk CONTEXT around the PR's hunks, so the moment the branch is refreshed onto the
# new base the BINDING digest moves while every AUTHORED line is byte-identical. Rules (a)
# and (b) both compare the binding, so before rule (c) that PR was refused as "the reviewed
# diff changed" — measured 2026-11-26 on four MERGEABLE, clean-reviewed PRs that produced
# zero merges. Rule (c) compares the PATCH projection instead, with the reviewed side
# re-derived from the marker's own signed `@ <sha>` and ANCHORED on its signed `diff=`.
#
# The fixtures below are the REAL BYTES of that measurement: a git repo where a PR inserts
# `PR-LINE` and `main` then inserts `MAIN-INSERT` inside the hunk's context window, with
# the branch merged forward. The digests are LITERALS pinned to the projection THIS file
# extracts from the workflow, and each is RECOMPUTED from the fixture before it is
# compared (see `check_patch`), so a pin that stops reproducing reddens instead of
# passing. ⛔ They are NOT pinned to the producer's bytes: measured 2026-11-27 against
# agent-infra `origin/main` `scripts/lib/diff-normalize.py --patch-only`, the producer
# DROPS the hunk frame entirely, so the two projections differ on every hunk-bearing
# entry and the digests below do NOT equal the producer's. That divergence is DECLARED
# in the workflow header (this consumer is the stricter half; agent-infra#4261 aligns the
# producer). A comment here used to claim these were "recomputed from agent-infra's
# --patch-only" and that "both sides changed together" — false, and unbackable: the
# harness never executes the producer.
#
#   reviewed binding dfebaa86…   patch b2434708…
#   live     binding 4d4d1118…   patch b2434708…   ← binding MOVED, patch IDENTICAL
#   one byte edited  patch c71ae6af…               ← a fresh review IS owed
#   whitespace added patch d99fac9b…               ← a fresh review IS owed (anti-patch-id)
#   RENAMED file     patch 98ae79a1…               ← a fresh review IS owed (path is identity)
#   DELETED line     patch 8662d5bd…               ← a fresh review IS owed
# The rename/delete pairs exist because the workflow header used to claim BOTH
# were "pinned by the harness" while no fixture carried either — a coverage
# claim the harness did not back. They are also checked to move AWAY from the
# reviewed digest, so neither pin can pass vacuously.
#   RELOCATED into another scope  patch a DIFFERENT digest  ← a fresh review IS owed (t20)
#   REWRITTEN branch             refused on ANCESTRY      ← a fresh review IS owed (t22)
#
# ⛔ THE PATCH LITERALS MOVED ONCE, DELIBERATELY (fresh review of #4255, P1). The
# projection used to drop the `@@ … @@` frame ENTIRELY, which made it position-blind:
# the same `+`/`-` lines RELOCATED from inside `def alpha()` to inside `def zulu()`
# projected IDENTICALLY, so a force-push that only MOVED the reviewed lines carried
# the verdict with no base advance at all. The frame is now kept with its
# enclosing-scope heading and without its numbers (`@@ l7` above — this fixture's hunk
# has the heading `l7`), and the digests above were recomputed from THIS projection.
# ⛔ THE HEADING DOES NOT CLOSE THE WHOLE CLASS: a relocation WITHIN one scope still
# projects identically (that is (t22)'s fixture, deliberately), and a base advance that
# rewrites the heading (a column-0 line inserted above the change) REFUSES — an
# over-block, documented in the workflow header. Both are declared, neither is silent.
REVIEWED_DIFF_FILE="$T/rev-base-moved.diff"
cat > "$REVIEWED_DIFF_FILE" <<'DIFFEOF'
diff --git a/f.md b/f.md
index 1b2a1c5..532af15 100644
--- a/f.md
+++ b/f.md
@@ -8,5 +8,6 @@ l7
 l8
 l9
 l10
+PR-LINE
 l11
 l12
DIFFEOF
LIVE_DIFF_FILE="$T/live-base-moved.diff"
cat > "$LIVE_DIFF_FILE" <<'DIFFEOF'
diff --git a/f.md b/f.md
index 819129e..2355478 100644
--- a/f.md
+++ b/f.md
@@ -8,6 +8,7 @@ l7
 l8
 l9
 l10
+PR-LINE
 l11
 l12
 MAIN-INSERT
DIFFEOF
EDITED_DIFF_FILE="$T/edited.diff"
sed 's/PR-LINE/PR-LINe/' "$LIVE_DIFF_FILE" > "$EDITED_DIFF_FILE"
WS_DIFF_FILE="$T/ws.diff"
sed 's/PR-LINE/PR-LINE /' "$LIVE_DIFF_FILE" > "$WS_DIFF_FILE"
# ── The other two claims the workflow header makes, as FIXTURES ───────────
# The header used to assert that "a renamed file and a deleted line" move the
# patch projection, "pinned by the harness" — while no fixture in this file
# contained either. These two close that gap: the rename exercises the
# `similarity index`/`rename from`/`rename to` lines (which the projection keeps
# VERBATIM, so the PATH is part of the identity), and the delete adds a `-` line.
RENAMED_DIFF_FILE="$T/renamed.diff"
cat > "$RENAMED_DIFF_FILE" <<'DIFFEOF'
diff --git a/old-name.md b/new-name.md
similarity index 87%
rename from old-name.md
rename to new-name.md
index 819129e..2355478 100644
--- a/old-name.md
+++ b/new-name.md
@@ -8,6 +8,7 @@ l7
 l8
 l9
 l10
+PR-LINE
 l11
 l12
DIFFEOF
DELETED_DIFF_FILE="$T/deleted.diff"
cat > "$DELETED_DIFF_FILE" <<'DIFFEOF'
diff --git a/f.md b/f.md
index 819129e..2355478 100644
--- a/f.md
+++ b/f.md
@@ -8,6 +8,5 @@ l7
 l8
 l9
 l10
+PR-LINE
-l11
 l12
DIFFEOF
# ── The three fields the projection KEEPS, as FIXTURES (review, P2) ──────────
# The header and the doc both promise the projection keeps `\ No newline at end of
# file`, a hunk-less entry (whose `index`/mode lines ARE the content) and BYTE-EXACT
# whitespace. `grep -c 'No newline'` over this file used to be 0, and the mode-only
# entry was asserted only against the BINDING normalizer, never via `check_patch` —
# so a refactor that dropped the hunk-less branch would make two different binaries
# (or two different modes) project identically and nothing here would redden.
NO_NEWLINE_DIFF_FILE="$T/no-newline.diff"
cat > "$NO_NEWLINE_DIFF_FILE" <<'DIFFEOF'
diff --git a/n.md b/n.md
index 1111111..2222222 100644
--- a/n.md
+++ b/n.md
@@ -1,3 +1,3 @@ l1
 a
-b
\ No newline at end of file
+c
\ No newline at end of file
DIFFEOF
# A HUNK-LESS entry: no `@@ ` line, so the projector must emit it WHOLE — the `index`
# line is the only thing distinguishing two different binaries here.
MODE_ONLY_DIFF_FILE="$T/mode-only.diff"
printf 'diff --git a/f.bin b/f.bin\nold mode 100644\nnew mode 100755\nindex 1111111..2222222\nBinary files a/f.bin and b/f.bin differ\n' \
    > "$MODE_ONLY_DIFF_FILE"
# CRLF: the projection splits on "\n", so a `\r` stays on every line and must survive
# the re-join byte-exactly (`printf`, not a heredoc, so the CRs are literal).
CRLF_DIFF_FILE="$T/crlf.diff"
printf 'diff --git a/c.txt b/c.txt\r\nindex 1111111..2222222 100644\r\n--- a/c.txt\r\n+++ b/c.txt\r\n@@ -1,2 +1,2 @@\r\n a\r\n-b\r\n+c\r\n' \
    > "$CRLF_DIFF_FILE"
PB_REVIEWED="dfebaa86f270853c99b5f809b31f6f5621009e8e5a0f03d3a42597470a62420c"
PB_LIVE="4d4d1118333448a5c97de826680e7fdcc929e133b179001565c03b2c80583eca"
PP_REVIEWED="b243470836a141b2808db9ce56a772217fc51aed6d919e01e74bb1f6f62bede8"
PP_EDITED="c71ae6af2931817321618d8ba92507d4096ba76e4d89c2352ea7b28604a438b7"
PP_WS="d99fac9ba2b8a910c951b5328e277d331f00ba752df3a5138245233f8e3f99e8"
PP_RENAME="98ae79a1c98bfe47651b1af658f653036c0e7f8b639545d43851a678e57a3014"
PP_DELETED="8662d5bdf1efd2faefdfb73fc90a334a4add05d73cac8b4b717afefc131453e0"
# The "the projector KEEPS this" pins (see the fixtures above): the hunk-less entry,
# the `\ No newline` marker, and CRLF bytes. Same rule as the rest — a literal digest,
# computed from the CONSUMER, never a hand-written claim.
PP_NONL="c6c0dc57cefc8b32abfe2ba54dc402930d04c0a661d5bba63b44c12bde5ec514"
PP_MODE="3cfab13a0536434519f83e1076377766ea01c78c1e7a1d2cfe8899d07db36fca"
PP_CRLF="8e358cf2901ff08963530c812753e90946859020bed42a9b09a3bc6b49643f43"
BASE_SHA="$(printf 'c%.0s' $(seq 1 40))"

# ── Direct vector tests of the extracted PATCH projector ──────────────────
PP_FN="$T/patch-projection-fn.sh"
awk '/^patch_projection_review_diff\(\) \{/ { f=1 } f { print } f && /^\}$/ { exit }' \
    "$RUN_BLOCK" > "$PP_FN"
if [ -s "$PP_FN" ] && grep -q '_IDX' "$PP_FN"; then
    ok "extracted patch_projection_review_diff from the run block"
    # shellcheck disable=SC1090
    source "$PP_FN"
else
    bad "could not extract patch_projection_review_diff (the rule-(c) vectors would pass vacuously)"
fi
check_patch() { # <label> <input-file> <expected-digest>
    local got
    got="$(patch_projection_review_diff < "$2" | openssl dgst -sha256 | awk '{print $NF}')"
    if [ "$got" = "$3" ]; then ok "patch projection [$1] -> pinned digest"; else bad "patch projection [$1] -> $got (want $3)"; fi
}
check_patch "the reviewed diff" "$REVIEWED_DIFF_FILE" "$PP_REVIEWED"
check_patch "the base-moved live diff (SAME authored change)" "$LIVE_DIFF_FILE" "$PP_REVIEWED"
check_patch "one byte edited" "$EDITED_DIFF_FILE" "$PP_EDITED"
check_patch "whitespace added" "$WS_DIFF_FILE" "$PP_WS"
check_patch "a RENAMED file" "$RENAMED_DIFF_FILE" "$PP_RENAME"
check_patch "a DELETED line" "$DELETED_DIFF_FILE" "$PP_DELETED"
check_patch 'a `\ No newline at end of file` marker' "$NO_NEWLINE_DIFF_FILE" "$PP_NONL"
check_patch 'a HUNK-LESS entry (mode/binary; the `index` line IS the content)' "$MODE_ONLY_DIFF_FILE" "$PP_MODE"
check_patch "CRLF bytes" "$CRLF_DIFF_FILE" "$PP_CRLF"
# …and each of the four "the authored change moved" vectors must ALSO differ
# from the reviewed digest. A pin that agrees with PP_REVIEWED would make the
# claim above vacuous, which is the failure mode the header sentence is about.
for _case in "a byte edit:$PP_EDITED" "whitespace:$PP_WS" \
             "a rename:$PP_RENAME" "a deleted line:$PP_DELETED" \
             "a no-newline marker:$PP_NONL" "a mode-only entry:$PP_MODE" \
             "CRLF bytes:$PP_CRLF"; do
    if [ "${_case##*:}" = "$PP_REVIEWED" ]; then
        bad "(t) the ${_case%%:*} vector projects to the REVIEWED digest — the anti-vacuity claim is false"
    else
        ok "(t) ${_case%%:*} moves the patch projection away from the reviewed digest"
    fi
done
unset _case
# The whole point, stated as a vector: the BINDING moves and the PATCH does not.
_bn="$(normalize_review_diff < "$REVIEWED_DIFF_FILE" | openssl dgst -sha256 | awk '{print $NF}')"
_bn2="$(normalize_review_diff < "$LIVE_DIFF_FILE" | openssl dgst -sha256 | awk '{print $NF}')"
if [ "$_bn" = "$PB_REVIEWED" ] && [ "$_bn2" = "$PB_LIVE" ] && [ "$_bn" != "$_bn2" ]; then
    ok "(t) the BINDING digest really MOVES on this base advance (so the case below is not vacuous)"
else
    bad "(t) binding digests are $_bn / $_bn2 (want $PB_REVIEWED / $PB_LIVE and DIFFERENT) — the fixture no longer models a base move"
fi

# ── (t1) THE FIX: a stale marker whose PATCH is unchanged carries ────────
diff_marker "$STALE" "$PB_REVIEWED" > "$T/body-t1"
STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t1"
assert_rc 0 "(t1) a base advance with an UNCHANGED authored change PASSES (rule (c))"
assert_contains "(t1) names the PATCH-match path" "passed via PATCH match"
if grep -qE -- "repos/${REPO_NAME}/compare/${BASE_SHA}\.\.\.${STALE}" "$T/gh.log"; then
    ok "(t1) the reviewed side was reconstructed from the marker's OWN signed @ sha (compare/<base>...<stale>)"
else
    bad "(t1) the gate never asked for the reviewed diff at the marker's sha (stub log: $(cat "$T/gh.log"))"
fi
if grep -qE -- "repos/${REPO_NAME}/pulls/${PR_NUMBER} --jq \.base\.sha" "$T/gh.log"; then
    ok "(t1) …against the PR's CURRENT base (so a moved base is what is being accounted for)"
else
    bad "(t1) the gate did not read the PR's base sha (stub log: $(cat "$T/gh.log"))"
fi
_t1_off="$(grep -vE -- "repos/${REPO_NAME}/(pulls/${PR_NUMBER}([^0-9]|\$)|issues/${PR_NUMBER}/comments|compare/[0-9a-f.]+)" "$T/gh.log" || true)"
if [ -z "$_t1_off" ]; then
    ok "(t1) every gh call is scoped to repos/${REPO_NAME} (set membership)"
else
    bad "(t1) gh call(s) outside this repo/PR: $(printf '%s' "$_t1_off" | tr '\n' '|')"
fi
[ -s "$T/gh-unrecognised" ] && bad "(t1) the gate made a gh call the stub did not recognise: $(cat "$T/gh-unrecognised")" || ok "(t1) no unrecognised gh call shape"

# ── (t2) ONE BYTE OF AUTHORED CHANGE STILL REQUIRES A FRESH REVIEW ────────
diff_marker "$STALE" "$PB_REVIEWED" > "$T/body-t2"
STUB_DIFF_FILE="$EDITED_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t2"
assert_rc 1 "(t2) one edited byte FAILS — the review does not carry"
assert_not_contains "(t2) …and does not claim a patch match" "passed via PATCH match"
assert_contains "(t2) …and says the diff changed" "does not match this PR's current diff hash"

# ── (t3) A WHITESPACE-ONLY EDIT IS AN EDIT (the anti-`patch-id` pin, at rule (c)) ──
diff_marker "$STALE" "$PB_REVIEWED" > "$T/body-t3"
STUB_DIFF_FILE="$WS_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t3"
assert_rc 1 "(t3) an added space FAILS — the patch projection is whitespace-exact"
assert_not_contains "(t3) …and does not claim a patch match" "passed via PATCH match"

# ── (t4) THE ANCHOR: a marker's `diff=` must be REPRODUCIBLE from its own sha ──
# The patch would match, but the reconstruction does not reproduce the signed digest —
# so rule (c) must refuse rather than compare a patch against bytes of unknown provenance.
diff_marker "$STALE" "$DH2" > "$T/body-t4"
STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t4"
assert_rc 1 "(t4) an UNREPRODUCIBLE diff= is refused even when the patch would match (the ANCHOR)"
assert_not_contains "(t4) …and no patch match is claimed" "passed via PATCH match"

# ── (t5) FAIL CLOSED: no reviewed side, no base sha, no live diff ─────────
diff_marker "$STALE" "$PB_REVIEWED" > "$T/body-t5"
STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" STUB_COMPARE_FAIL=1 run_gate "$T/body-t5"
assert_rc 1 "(t5) a FAILED compare fetch fails closed (never a pass from a failed read)"
# A compare answer that is NON-EMPTY but carries no `diff --git` entry: there is
# no reviewed artifact to anchor on, so rule (c) must refuse. NOTE ON
# ATTRIBUTABILITY: deleting the compare-fetch guard (`gate.yml` `[ ! -s ]` /
# `grep -qE '^diff --git '`) does NOT redden this row, because the ANCHOR refuses
# on such a file anyway. The guard is therefore REASONED-redundant with the
# anchor, not unattributed-by-oversight — an attacker would have to hold the
# signing key to mint a `diff=` digest over a non-diff, and then they can mint a
# valid review marker. The row pins the BEHAVIOUR (fail closed), not the guard.
printf 'Not a diff at all\n' > "$T/ntbd.diff"
STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$T/ntbd.diff" run_gate "$T/body-t5"
assert_rc 1 "(t5) a compare answer with NO diff --git entry fails closed (no patch match)"
assert_not_contains "(t5) …and no patch match is claimed" "passed via PATCH match"
STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" STUB_BASE_FAIL=1 \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t5"
assert_rc 1 "(t5) an UNREADABLE base sha fails closed"
STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" STUB_DIFF_FAIL=1 \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t5"
assert_rc 1 "(t5) an unreadable LIVE diff fails closed (no patch digest to compare)"

# ── (t6) THE GREEN PATH PAYS NOTHING EXTRA ─────────────────────────────
# Rule (c) is consulted only when (a) and (b) found nothing, so an ordinary PR makes
# no compare call and no base-sha read — otherwise every gate run would pay two extra
# API calls, and the queue's entry check is on the critical path of every merge.
# ⛔ THE FIXTURE IS DELIBERATELY A `diff=`-BEARING HEAD-BOUND MARKER **PLUS** a SIGNED
# STALE SIBLING whose patch WOULD match. With `legacy_marker "$HEAD"` alone (no
# `diff=`) the old fixture could not attribute the cost guard `[ -z "$candidates" ]`:
# the stale sibling is what the guard is keeping OFF the probe path, so if that guard
# is deleted the sibling gets probed and this row's "no API call" assertion reddens.
{ diff_marker "$HEAD" "$PP_REVIEWED"; diff_marker "$STALE" "$PB_REVIEWED"; } > "$T/body-t6"
STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t6"
assert_rc 0 "(t6) a HEAD-BOUND marker still passes on rule (a)"
if grep -qE -- "/compare/|--jq \.base\.sha" "$T/gh.log"; then
    bad "(t6) the green path made a rule-(c) API call (log: $(cat "$T/gh.log")) — rule (c) must cost nothing when (a)/(b) decide"
else
    ok "(t6) the green path made NO rule-(c) API call"
fi

# ── (t7) MUTATION: remove the patch comparison and (t1) must FAIL ────────
# Proves (t1) is the rule's doing, not an accident of the fixtures.
MUT_PP="$T/mutant-no-patch-carry.sh"
sed 's/^  if \[ "$p_patch" != "$live_patch_hash" \]; then$/  if true; then/' "$RUN_BLOCK" > "$MUT_PP"
if cmp -s "$RUN_BLOCK" "$MUT_PP"; then
    bad "(t7) the mutation did not apply — the anchored comparison line changed shape, so this control proves NOTHING"
else
    _saved_block="$RUN_BLOCK"; RUN_BLOCK="$MUT_PP"
    STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
        STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t1"
    RUN_BLOCK="$_saved_block"
    # ⛔ NOT `[ … ]; ck_rc=$?`. The harness runs under `set -euo pipefail`, so a bare
    # failing `[ ]` TERMINATES the shell: this control's own `bad` diagnostic would be
    # dead in exactly the case it exists for (the mutant PASSING), and the suite would
    # die mid-run with no ❌ row and no PASS=/FAIL= summary — the anti-pattern this file
    # already documents ~20 lines above. `if` puts the test in a condition context.
    if [ "$GATE_RC" = "1" ]; then
        ok "(t7) the MUTANT FAILS the unchanged-artifact case — so (t1) is rule (c)'s doing"
    else
        bad "(t7) the MUTANT passed (rc=$GATE_RC) — (t1) does not depend on the patch comparison"
    fi
fi

# ── (t8) MUTATION: the COST GATE is load-bearing, and it is the fixture's doing ──
# Deleting `[ -z "$candidates" ] &&` makes rule (c) probe the SIGNED STALE SIBLING of
# (t6). If that mutant does NOT produce a rule-(c) API call, then (t6) is decorative
# and a later refactor that drops the guard would ship with the suite green.
MUT_COST="$T/mutant-no-cost-gate.sh"
# NOTE the indentation: the harness STRIPS the YAML's 10-space `run: |` prefix, so a
# top-level statement in the extracted block starts at column 0 and a `patch_arm_ok`
# body line at column 2 (which is why (t7)'s mutant pattern above uses 2).
sed 's/^if \[ "$candidates_acceptable" -eq 0 \] && \[ -n "$all_markers" \]; then$/if [ -n "$all_markers" ]; then/' \
    "$RUN_BLOCK" > "$MUT_COST"
if cmp -s "$RUN_BLOCK" "$MUT_COST"; then
    bad "(t8) the cost-gate mutation did not apply — the guard line changed shape, so this control proves NOTHING"
else
    _saved_block="$RUN_BLOCK"; RUN_BLOCK="$MUT_COST"
    STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
        STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t6"
    RUN_BLOCK="$_saved_block"
    if grep -qE -- "/compare/|--jq \.base\.sha" "$T/gh.log"; then
        ok "(t8) with the cost gate REMOVED the stale sibling IS probed — so (t6) attributes that guard"
    else
        bad "(t8) the mutant produced NO rule-(c) API call — (t6) does not attribute the cost gate (guard still in place? fixture not signed?)"
    fi
    # …and the HEAD-BOUND skip inside patch_arm_ok is a second, DEFENCE-IN-DEPTH belt:
    # while the cost gate holds it is unreachable, so removing it ALONE is invisible to
    # every row. Removing BOTH must still be caught — that is what this second mutant
    # buys, and it is stated here rather than left as an implied attribution.
    MUT_BOTH="$T/mutant-no-cost-gate-no-head-skip.sh"
    sed -e 's/^if \[ "$candidates_acceptable" -eq 0 \] && \[ -n "$all_markers" \]; then$/if [ -n "$all_markers" ]; then/' \
        -e 's#^  \[ "$p_sha" != "$expected" \] || return 1$#  :#' \
        "$RUN_BLOCK" > "$MUT_BOTH"
    if cmp -s "$RUN_BLOCK" "$MUT_BOTH"; then
        bad "(t8) the double mutation did not apply — so the head-bound-skip claim below is unchecked"
    else
        _saved_block="$RUN_BLOCK"; RUN_BLOCK="$MUT_BOTH"
        { diff_marker "$HEAD" "$PP_REVIEWED"; } > "$T/body-t8b"
        STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
            STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t8b"
        RUN_BLOCK="$_saved_block"
        if grep -qE -- "/compare/" "$T/gh.log"; then
            ok "(t8) with BOTH the cost gate and the head-bound skip removed the head-bound marker IS probed — the belt is caught in combination"
        else
            bad "(t8) the double mutant produced no compare call — the head-bound skip has NO attributable catch"
        fi
    fi
fi

# Reviewed-diff compare counting (review cycle 3): the DECISIVE call is the one
# carrying the diff MEDIA TYPE. A bare `compare/<base>...` count also counts the
# BRANCH-SIDE read, which is a SECOND call per probe — a distinction invisible while
# the fixture graph was empty (the read never ran), i.e. those assertions used to pass
# for the wrong reason. The live-diff call is a `/pulls/` URL, so it is not matched.
reviewed_diff_compares() { # <gh-log>
    grep -E -- "compare/" "$1" | grep -cF -- "application/vnd.github.v3.diff" || true
}

# ── (t9) THE PROBE FAN-OUT IS BOUNDED BY EVIDENCE, NOT BY BODY BYTES ─────
# `all_markers` is attacker-supplied text: the PR BODY is not restricted to
# OWNER|MEMBER|COLLABORATOR and this workflow is `pull_request_target`, so a fork PR's
# body reaches rule (c). Before the bound, every well-formed line cost TWO repo-scoped
# `gh api` calls against the 1,000/hour PER-REPOSITORY token budget that every workflow
# shares — and this check is the merge queue's ENTRY condition, so exhausting it reds
# the merge path for EVERY PR. Measured pre-fix on a 25-line forged body: 53 calls.
FORGED="$T/body-t9-forged"
: > "$FORGED"
for _i in $(seq 1 25); do
    # Well-formed marker shape, distinct sha+diff, but a signature the gate cannot
    # verify — i.e. exactly what a fork PR author can write for free.
    printf 'review recorded: reviews/%s.json verdict=clean @ %s diff=%s (%s) sig=%s\n' \
        "$PR_NUMBER" "$(printf '%040d' "$_i")" "$(printf '%064d' "$_i")" \
        "$REPO_NAME" "$(printf 'f%.0s' $(seq 1 64))" >> "$FORGED"
done
diff_marker "$STALE" "$PB_REVIEWED" >> "$FORGED"
STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$FORGED"
assert_rc 0 "(t9) 25 forged marker lines beside ONE signed record: the signed record still carries"
_t9_compares="$(reviewed_diff_compares "$T/gh.log")"
_t9_all_calls="$(grep -cE -- '/compare/|--jq \.base\.sha' "$T/gh.log" || true)"
if [ "$_t9_compares" -le 1 ] && [ "$_t9_all_calls" -le 4 ]; then
    ok "(t9) the forged lines bought NO probe (${_t9_compares} reviewed-diff compare(s), ${_t9_all_calls} rule-(c) call(s) — signature verified FIRST)"
else
    bad "(t9) the body's forged lines bought ${_t9_compares} reviewed-diff compare(s) / ${_t9_all_calls} rule-(c) call(s) — the probe fan-out is a function of PR TEXT"
fi
if grep -qE -- "/compare/[0-9a-f]+\.\.\.0{20}" "$T/gh.log"; then
    bad "(t9) a forged marker's sha reached a compare probe (log: $(cat "$T/gh.log"))"
else
    ok "(t9) no forged sha reached the compare endpoint"
fi
# And the CAP is real: many DISTINCT SIGNED records cannot buy unbounded probes.
CAP_PIN="$(sed -nE 's/^ *PATCH_PROBE_CAP=([0-9]+).*/\1/p' "$RUN_BLOCK" | head -1)"
if [[ "$CAP_PIN" =~ ^[0-9]+$ ]]; then
    CAPBODY="$T/body-t9-cap"
    : > "$CAPBODY"
    for _i in $(seq 1 $((CAP_PIN + 3))); do
        _m="review recorded: reviews/${PR_NUMBER}.json verdict=clean @ $(printf '%040d' "$_i") diff=$(printf '%064d' "$_i") (${REPO_NAME})"
        printf '%s sig=%s\n' "$_m" "$(sign "$_m")" >> "$CAPBODY"
    done
    STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
        STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$CAPBODY"
    assert_rc 1 "(t9) signed stale records that match nothing still FAIL closed"
    # ⛔ COUNT THE SHAPE, NOT THE ENDPOINT. Since the ancestry precondition the gate
    # makes up to FOUR calls per probe (base sha, the ancestry read, the branch-side
    # commit list, and the reviewed diff) and
    # ALL of them are repo-scoped `gh api` calls against the shared budget — so the
    # assertion is two-sided: the EXPENSIVE per-probe compare is exactly CAP_PIN, and
    # the rule-(c) call total is bounded by 4 x CAP_PIN. Counting `/compare/` alone
    # would have read a correct multi-call probe as a cap violation (measured: 16 for 8
    # when it was three calls and the comment said two).
    # ⛔ AND COUNT THE DECISIVE CALL, NOT EVERY URL THAT STARTS WITH IT (review cycle 3):
    # the BRANCH-SIDE read also fetches `compare/<base>...`, so a bare
    # `compare/${BASE_SHA}\.\.\.` count reads TWO calls per probe once the branch-side
    # read actually runs — which it did not while the fixture graph was empty. That made
    # this assertion pass for the wrong reason and would report a cap violation (or mask
    # one) after any fixture change. A reviewed-diff compare is the one that carries the
    # diff MEDIA TYPE; the live-diff call is a `/pulls/` URL and so is not matched here.
    _t9_cap_compares="$(reviewed_diff_compares "$T/gh.log")"
    _t9_cap_all="$(grep -cE -- '/compare/|--jq \.base\.sha' "$T/gh.log" || true)"
    if [ "$_t9_cap_compares" -eq "$CAP_PIN" ]; then
        ok "(t9) the probe cap holds (${_t9_cap_compares} reviewed-diff compares for $((CAP_PIN + 3)) signed records, cap $CAP_PIN)"
    else
        bad "(t9) $_t9_cap_compares reviewed-diff compare calls for $((CAP_PIN + 3)) signed records (cap is $CAP_PIN) — the bound is not enforced"
    fi
    if [ "$_t9_cap_all" -le $((CAP_PIN * 4)) ]; then
        ok "(t9) …and the TOTAL rule-(c) API spend is bounded (${_t9_cap_all} calls ≤ 4 x $CAP_PIN)"
    else
        bad "(t9) $_t9_cap_all rule-(c) calls for a $CAP_PIN-probe cap — a probe costs at most 4, so the bound leaked"
    fi
    assert_contains "(t9) …and the cap is reported, not silent" "stopped after ${CAP_PIN} distinct SIGNED patch probes"
else
    bad "(t9) could not read PATCH_PROBE_CAP from the run block — the bound is unassertable"
fi

# ── (t10) ONE SIGNATURE VERIFIER, AND TWO DIFFERENT SIGNATURE DIAGNOSES ───
# Two P3 follow-ups from the review at 5ed119f05dbe, both MEASURED here rather than
# left as prose. Neither can produce a wrong verdict today; both can mislead the
# next reader, which is how a guard's coverage claim stops being true.
#
# F1: the comment above `marker_sig_ok` claims ONE verifier "shared by the rule-(c)
#     probe and the acceptance loop below", while the acceptance loop carried its own
#     inline copy of the HMAC pipeline. The copies agreed, so no verdict moved — but a
#     future edit to the function would have covered only the probe. The fix makes the
#     claim TRUE (the loop calls the function); this row is what keeps it true: a
#     second HMAC pipeline anywhere in the run block reddens it.
_h1="$(grep -c 'openssl dgst -sha256 -hmac' "$RUN_BLOCK" || true)"
if [ "$_h1" = "1" ]; then
    ok "(t10) exactly ONE HMAC verification site in the run block (the probe and the acceptance loop share it)"
else
    bad "(t10) $_h1 HMAC verification site(s) in the run block — the \"one verifier\" claim is false again, and an edit to marker_sig_ok would cover only the probe"
fi

# F2: `patch_sig_rejected` counted EVERY unverifiable marker and the diagnostic
#     advised "the recording KEY is the problem — not the diff". For a STALE marker
#     that also carries `diff=` that is right: a correct key would have made it
#     probeable. For a LEGACY marker (no `diff=`) rule (c) has no digest to anchor on,
#     so no key could ever have made it probeable — the advice sent the operator to
#     re-mint a key that was fine. Both halves are pinned: the probeable case MUST
#     name the key, the unprobeable one MUST NOT.
BAD_SIG="$(printf 'f%.0s' $(seq 1 64))"
{
    printf 'review recorded: reviews/%s.json verdict=clean @ %s diff=%s (%s) sig=%s\n' \
        "$PR_NUMBER" "$STALE" "$PB_REVIEWED" "$REPO_NAME" "$BAD_SIG"
} > "$T/body-t10-probeable"
STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t10-probeable"
assert_rc 1 "(t10) a STALE diff= marker with an UNVERIFIABLE signature fails closed"
assert_contains "(t10) …and names the recording KEY as the problem (a correct key WOULD have made it probeable)" \
    "the recording KEY is the problem"
{
    printf 'review recorded: reviews/%s.json verdict=clean @ %s (%s) sig=%s\n' \
        "$PR_NUMBER" "$STALE" "$REPO_NAME" "$BAD_SIG"
} > "$T/body-t10-legacy"
STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t10-legacy"
assert_rc 1 "(t10) a LEGACY stale marker with an UNVERIFIABLE signature fails closed"
assert_not_contains "(t10) …and does NOT blame the key for a marker rule (c) could never probe" \
    "the recording KEY is the problem"
assert_contains "(t10) …and tells the operator to re-record instead" \
    "re-record on the current head"


# ── (t10b) THE RULE-(c) DIAGNOSTICS SURVIVE THE HMAC-MISMATCH EXIT ───────────
# ⛔ THE POPULATION THE FIX EXISTS FOR HAD NO ROW (final review). `patch_sig_rejected`
# and `patch_shape_rejected` used to be emitted inside the `[ -z "$marker" ]`
# classification block, which runs AFTER the HMAC-mismatch branch — so whenever the body
# ALSO carried a decoy candidate (the shape the new entry condition serves), the gate
# exited on "HMAC mismatch" and never printed the reason rule (c) refused. Measured
# before the fix: decoy + wrong-key stale `diff=` marker printed 0 of those lines.
# Both blocks now precede that branch, guarded on `[ -z "$marker" ]`.
#
# ⛔ AND THE GUARD IS PINNED IN THE DIRECTION A REFACTOR BREAKS IT: the second half
# asserts the diagnostic is ABSENT on a run that CARRIES. Moving the block back below
# the exit — or dropping the `[ -z "$marker" ]` guard, which makes a GREEN run print a
# false `::error::` — reddens here and nowhere else.
DECOY_HEAD="review recorded: reviews/${PR_NUMBER}.json verdict=clean @ ${HEAD} (${REPO_NAME})"
BAD_SIG_T10B="$(printf 'e%.0s' $(seq 1 64))"
{
    printf '%s sig=%s\n' "$DECOY_HEAD" "$BAD_SIG_T10B"
    printf 'review recorded: reviews/%s.json verdict=clean @ %s diff=%s (%s) sig=%s\n' \
        "$PR_NUMBER" "$STALE" "$PB_REVIEWED" "$REPO_NAME" "$BAD_SIG_T10B"
} > "$T/body-t10b-decoy"
STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t10b-decoy"
assert_rc 1 "(t10b) a decoy beside a wrong-key stale diff= marker still fails closed"
assert_contains "(t10b) …and the operator is TOLD the stale marker's signature is the problem, despite the HMAC-mismatch exit" \
    "STALE-sha evidence marker(s) carried BOTH"
assert_contains "(t10b) …and both diagnoses are printed, not one" "HMAC mismatch"
cp "$T/gate.out" "$T/gate-t10b-decoy.out"
# The anti-vacuity half: a VALID stale record carries, and must NOT print that error.
{
    printf '%s sig=%s\n' "$DECOY_HEAD" "$BAD_SIG_T10B"
    # one marker per remainder class, each with a bad signature, so BOTH absence rows
    # below can actually fail: a stale+diff= one (key class) and a legacy one (shape).
    printf 'review recorded: reviews/%s.json verdict=clean @ %s diff=%s (%s) sig=%s\n' \
        "$PR_NUMBER" "$STALE" "$PB_REVIEWED" "$REPO_NAME" "$BAD_SIG_T10B"
    printf 'review recorded: reviews/%s.json verdict=clean @ %s (%s) sig=%s\n' \
        "$PR_NUMBER" "$STALE" "$REPO_NAME" "$BAD_SIG_T10B"
    diff_marker "$STALE" "$PB_REVIEWED"
} > "$T/body-t10b-carry"
STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t10b-carry"
assert_rc 0 "(t10b) a decoy beside a VALID stale record still carries"
# ⛔ WHICH DIAGNOSTIC CAN A CARRY EVEN PRINT? All three classes can, so the absence rows
# below are falsifiable — but an earlier revision of the KEY row was not, and the reason
# is worth keeping: in THAT fixture the only bad-signature marker was head-bound, so
# `patch_sig_rejected` was 0 under any mutation and the assertion could never fail. The
# carry body now also carries a LEGACY stale bad-signature marker, so the shape class is
# genuinely present, and a third marker with a stale sha + `diff=` + bad signature makes
# the key class present too. What keeps a green run clean is the `[ -z "$marker" ]` guard.
assert_not_contains "(t10b) …and prints NO diagnostic for its head-bound decoy on a carry (the empty-marker guard)" \
    "are HEAD-BOUND"
assert_not_contains "(t10b) …and no shape-error either (the third class the same guard covers)" \
    "failed the signature check AND lack the shape"
# ORDER, on the diagnostics the hoist actually moved: the key diagnosis must appear
# BEFORE the HMAC-mismatch line, or the operator reads "fix your key" without the reason
# the carry was refused. `|| true` on both probes on purpose — under `set -euo pipefail`
# a bare `grep | head | cut` assignment TERMINATES the run in exactly the case the row
# detects (the line absent), which would kill every row after it.
_kt_ln="$(grep -n 'STALE-sha evidence marker(s) carried BOTH' "$T/gate-t10b-decoy.out" | head -1 | cut -d: -f1 || true)"
_hm_ln="$(grep -n 'HMAC mismatch' "$T/gate-t10b-decoy.out" | head -1 | cut -d: -f1 || true)"
if [ -n "$_kt_ln" ] && { [ -z "$_hm_ln" ] || [ "$_kt_ln" -lt "$_hm_ln" ]; }; then
    ok "(t10b) …and the key diagnosis PRECEDES the HMAC-mismatch line (order, not just presence)"
else
    bad "(t10b) the key diagnosis at line ${_kt_ln:-none} does not precede the HMAC line ${_hm_ln:-none} — the hoist is not doing its job"
fi
unset _kt_ln _hm_ln
# MUTATION: drop that guard, and the carry above must sprout a false ::error::.
MUT_GUARD="$T/mutant-no-marker-guard-on-diag.sh"
# The OUTER guard is the one that matters: the two diagnostics are nested inside
# `if [ -z "$marker" ]`, so mutating an inner condition alone changes nothing (measured).
# `0,/re/` replaces only the FIRST match, which is the hoisted wrapper — the
# classification block's own `[ -z "$marker" ]` is the second and must stay.
sed '0,/^if \[ -z "\$marker" \]; then$/s//if true; then/' \
    "$RUN_BLOCK" > "$MUT_GUARD"
if cmp -s "$RUN_BLOCK" "$MUT_GUARD"; then
    bad "(t10b) the diagnostic-guard mutation did not apply — this control proves NOTHING"
else
    _saved_block="$RUN_BLOCK"; RUN_BLOCK="$MUT_GUARD"
    STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
        STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t10b-carry"
    RUN_BLOCK="$_saved_block"
    if grep -q "are HEAD-BOUND" <<<"$GATE_OUT"; then
        ok "(t10b) the MUTANT annotates a GREEN run with a head-bound key error — so the guard is load-bearing"
    else
        bad "(t10b) the mutant printed no head-bound key error on the carry — the not_contains assertion above attributes nothing"
    fi
    # …and the OTHER two absence rows are falsifiable too: with the guard dropped, the
    # key and shape diagnostics must ALSO appear on this green run (one marker of each
    # class is in the body precisely so this can be checked).
    _miss=""
    grep -q "STALE-sha evidence marker(s) carried BOTH" <<<"$GATE_OUT" \
        || _miss="${_miss} key"
    grep -q "failed the signature check AND lack the shape" <<<"$GATE_OUT" \
        || _miss="${_miss} shape"
    if [ -z "$_miss" ]; then
        ok "(t10b) …and the key and shape absence rows are both falsifiable (both classes print on the mutant)"
    else
        bad "(t10b) the mutant did not print:${_miss} — those absence rows cannot fail and pin nothing"
    fi
    unset _miss
fi


# ── (u) END TO END ON REAL GIT BYTES: a base advance that rewrites hunk context ──
# ⛔ WHY THIS SECTION EXISTS. Every case above feeds the gate a HAND-WRITTEN diff, so
# all of them pass if the fixtures encode the author's belief about what a base
# advance does. This one uses a real git repository: it derives the reviewed bytes and
# the live bytes from `git diff` itself, advanced the base for real, and then asks the
# production gate step for a verdict on each. If the premise of #4255 is wrong — if a
# base advance does NOT move the rendered diff, or if it moves the authored lines too —
# this section says so instead of agreeing with the code under test.
#
# THE MODEL (and why it is the production shape, not a simplification):
#   base1 ──► R (the reviewed commit)           `diff= ` is signed over `diff base1...R`
#     └──► base2 (main advances: lines inserted ABOVE the edited region)
#   feat merges base2 ──► H (the head the gate sees)
# A merge (not a rebase) keeps R reachable, exactly as a "Update branch" click does, so
# `compare/<current base>...<R>` still answers — which is the ANCHOR rule (c) needs.
U_REPO="$T/u-repo"
U_G() { git -C "$U_REPO" -c user.email=t@example.invalid -c user.name=t \
                              -c commit.gpgsign=false -c init.defaultBranch=main "$@"; }
mkdir -p "$U_REPO"
git -c init.defaultBranch=main init -q "$U_REPO" || bad "(u) could not create the scratch repository"
# ⛔ THE CONTEXT LINES ARE INDENTED ON PURPOSE. git derives a hunk's SECTION HEADING
# from the line preceding the hunk using its funcname heuristic, and for a file with no
# driver that heuristic matches a line starting in column 0 — so a base advance that
# inserted `upstream A` above the change ALSO changed the heading (`context line 18` →
# `context line 20`) and the projection rightly refused. That is an over-block, and it
# would have hidden the carry this section exists to demonstrate. Indented lines match
# no funcname, so the heading is empty (`@@`) on both sides and the ONLY thing that
# moves is what a base advance is allowed to move.
{
  for n in $(seq 1 40); do printf '  context line %02d\n' "$n"; done
} > "$U_REPO/doc.txt"
U_G add doc.txt && U_G commit -qm "base1" && U_BASE1="$(U_G rev-parse HEAD)"
U_G checkout -qb feat
# The AUTHORED change: replace lines 20-22. Byte-for-byte what a reviewer approves.
python3 - "$U_REPO/doc.txt" <<'PYEOF'
import sys
p = sys.argv[1]
lines = open(p).read().split("\n")
lines[19:22] = ["AUTHORED one", "AUTHORED two", "AUTHORED three"]
open(p, "w").write("\n".join(lines))
PYEOF
U_G commit -qam "feat: the reviewed change" && U_R="$(U_G rev-parse HEAD)"
U_REVIEWED_DIFF="$T/u-reviewed.diff"
U_G diff "$U_BASE1...$U_R" > "$U_REVIEWED_DIFF"
# `diff=` is the digest the PRODUCER signs at review time: sha256 over the NORMALIZED
# reviewed diff (live_diff_hash_norm). Derived here from the extracted normalizer, so
# the two sides cannot agree by both being wrong.
U_MARKER_DIFF="$(normalize_review_diff < "$U_REVIEWED_DIFF" | openssl dgst -sha256 | awk '{print $NF}')"
# main advances with work in the SAME FILE, INSIDE the authored hunk's context window.
# ⛔ THIS PLACEMENT IS THE WHOLE FIXTURE. A change far from the edited region only moves
# LINE NUMBERS, which the normalizer rewrites (`@@ -N,…` → `@@ -0,…`), so rule (b) still
# matches and nothing is demonstrated — measured here first: three lines inserted at the
# top of the file left the normalized digest BYTE-IDENTICAL and (u2) passed on the
# pre-fix gate. Landing the upstream work adjacent to the authored lines changes the
# hunk's CONTEXT LINES, which the normalizer keeps — that is the #4255 mechanism.
U_G checkout -q main
python3 - "$U_REPO/doc.txt" <<'PYEOF'
import sys
p = sys.argv[1]
lines = open(p).read().split("\n")
lines[18:18] = ["upstream A", "upstream B"]   # immediately above the authored region
open(p, "w").write("\n".join(lines))
PYEOF
U_G commit -qam "main: unrelated work above the edited region"
U_BASE2="$(U_G rev-parse HEAD)"
U_G checkout -q feat
U_G merge -q --no-edit main >/dev/null 2>&1 || bad "(u) the branch would not merge the advanced base"
U_G commit -q --allow-empty -m "merge base" 2>/dev/null || true
U_HEAD="$(U_G rev-parse HEAD)"
U_LIVE_DIFF="$T/u-live.diff"
U_G diff "$U_BASE2...$U_HEAD" > "$U_LIVE_DIFF"
# The REVIEWED side as the gate reconstructs it (rule (c)): from the marker's @ sha
# against the CURRENT base. Identical to the review-time bytes iff R is unreachable
# from base2 — the property the whole rule rests on.
U_REVIEWED_AT_BASE2="$T/u-reviewed-at-base2.diff"
U_G diff "$U_BASE2...$U_R" > "$U_REVIEWED_AT_BASE2"

# ── u1: the PREMISE. Prove the fixture does what #4255 claims, before trusting it. ──
if [ -s "$U_LIVE_DIFF" ] && [ -s "$U_REVIEWED_DIFF" ] && cmp -s "$U_REVIEWED_DIFF" "$U_REVIEWED_AT_BASE2"; then
    ok "(u1) the reviewed bytes are reproducible from the advanced base ($(wc -l < "$U_REVIEWED_DIFF" | tr -d ' ') lines) — the ANCHOR rule (c) depends on can be answered"
else
    bad "(u1) the reviewed diff is NOT reproducible from the advanced base, so the fixture does not model the production shape"
fi
U_LIVE_NORM="$(normalize_review_diff < "$U_LIVE_DIFF" | openssl dgst -sha256 | awk '{print $NF}')"
U_LIVE_PATCH="$(patch_projection_review_diff < "$U_LIVE_DIFF" | openssl dgst -sha256 | awk '{print $NF}')"
U_REV_PATCH="$(patch_projection_review_diff < "$U_REVIEWED_AT_BASE2" | openssl dgst -sha256 | awk '{print $NF}')"
if [ "$U_LIVE_NORM" != "$U_MARKER_DIFF" ]; then
    ok "(u1) the base advance MOVED the rendered digest (${U_MARKER_DIFF:0:12}… → ${U_LIVE_NORM:0:12}…), so rules (a)/(b) cannot carry this record — the measured harm"
else
    bad "(u1) the base advance did NOT move the rendered digest — this fixture cannot demonstrate anything, because rule (b) would already carry it"
fi
if [ "$U_REV_PATCH" = "$U_LIVE_PATCH" ]; then
    ok "(u1) and the PATCH projection is unmoved ($(printf '%s' "$U_LIVE_PATCH" | cut -c1-12)…), so the authored change is the one that was reviewed"
else
    bad "(u1) the patch projection moved too — git says the authored change differs, so rule (c) must refuse"
fi

# ── u2: PRE-FIX BEHAVIOUR, on those same real bytes → RED ────────────────────────
# `STUB_BASE_SHA` empty is exactly the pre-#4255 gate: rule (c) refuses (it never
# existed), and the run is otherwise identical. This is the direction the issue reports.
printf '%s\n' "$(diff_marker "$U_R" "$U_MARKER_DIFF")" > "$T/body-u"
_saved_head="$HEAD"; HEAD="$U_HEAD"
STUB_DIFF_FILE="$U_LIVE_DIFF" STUB_BASE_SHA="" run_gate "$T/body-u"
assert_rc 1 "(u2) PRE-FIX: the gate is RED on a base advance that changed nothing the reviewer approved (the harm #4255 reports)"
# ── u3: THE FIX, same bytes, rule (c) available → GREEN ──────────────────────────
STUB_DIFF_FILE="$U_LIVE_DIFF" STUB_BASE_SHA="$U_BASE2" \
    STUB_COMPARE_FILE="$U_REVIEWED_AT_BASE2" run_gate "$T/body-u"
assert_rc 0 "(u3) FIXED: the SAME record carries at the advanced head, with no new review"
assert_contains "(u3) names the patch path" "passed via PATCH match"

# ── u4: ONE AUTHORED BYTE CHANGES → RED AGAIN, rule (c) and all ──────────────────
# The counter-direction, on the same repository: edit one character of an AUTHORED
# line and commit it. If the fix carries that, the gate is worthless.
python3 - "$U_REPO/doc.txt" <<'PYEOF'
import sys
p = sys.argv[1]
s = open(p).read()
open(p, "w").write(s.replace("AUTHORED two", "AUTHORED two!", 1))
PYEOF
U_G commit -qam "feat: an authored byte changed after the review"
U_HEAD2="$(U_G rev-parse HEAD)"
U_LIVE2_DIFF="$T/u-live2.diff"
U_G diff "$U_BASE2...$U_HEAD2" > "$U_LIVE2_DIFF"
HEAD="$U_HEAD2"
STUB_DIFF_FILE="$U_LIVE2_DIFF" STUB_BASE_SHA="$U_BASE2" \
    STUB_COMPARE_FILE="$U_REVIEWED_AT_BASE2" run_gate "$T/body-u"
assert_rc 1 "(u4) ONE authored byte changed → RED (rule (c) does not carry a changed change)"
# ── u5: WHITESPACE ONLY → RED TOO (anti-`patch-id`, on real git bytes) ───────────
# `git patch-id --stable` ignores whitespace and would MATCH here; the projection must
# not, because the reviewer approved the bytes, not their meaning.
U_G checkout -q feat
U_G reset -q --hard "$U_HEAD"
python3 - "$U_REPO/doc.txt" <<'PYEOF'
import sys
p = sys.argv[1]
open(p, "w").write(open(p).read().replace("AUTHORED three", "AUTHORED  three", 1))
PYEOF
U_G commit -qam "feat: whitespace only"
U_HEAD3="$(U_G rev-parse HEAD)"
U_LIVE3_DIFF="$T/u-live3.diff"
U_G diff "$U_BASE2...$U_HEAD3" > "$U_LIVE3_DIFF"
HEAD="$U_HEAD3"
STUB_DIFF_FILE="$U_LIVE3_DIFF" STUB_BASE_SHA="$U_BASE2" \
    STUB_COMPARE_FILE="$U_REVIEWED_AT_BASE2" run_gate "$T/body-u"
assert_rc 1 "(u5) a WHITESPACE-only edit → RED (the projection is byte-exact; \`patch-id\` would have carried it)"
HEAD="$_saved_head"


# ── (t20) A RELOCATION IS NOT A CARRY (fresh review of #4255, P1) ────────────
# The projection drops CONTEXT and the hunk's line numbers, so before this case nothing in
# it was POSITION-BEARING. Measured by the fresh review: the same `+GATE_BYPASS` reviewed
# inside `def alpha()` and shipped inside `def zulu()` projected IDENTICALLY, so a
# force-push that only MOVED the reviewed lines carried the verdict — with no base advance
# at all, which is not the harm this rule exists for. Keeping the hunk frame's
# enclosing-scope heading closes THAT half of the class (a relocation between scopes).
# ⛔ It does NOT close a relocation WITHIN one scope, and it is not free: git derives the
# heading from the line preceding the hunk, so a base advance inserting a column-0 line
# above the change rewrites it and the carry is REFUSED (an over-block, measured — see
# §(u)'s "CONTEXT LINES ARE INDENTED ON PURPOSE" note, which is why that fixture cannot
# see it). The same-scope half is closed by the ANCESTRY precondition, pinned in (t22).
RELOC_REVIEWED="$T/reloc-reviewed.diff"
cat > "$RELOC_REVIEWED" <<'DIFFEOF'
diff --git a/g.py b/g.py
index 1111111..2222222 100644
--- a/g.py
+++ b/g.py
@@ -3,3 +3,4 @@ def alpha():
     a1
     a2
+GATE_BYPASS
     a3
DIFFEOF
RELOC_LIVE="$T/reloc-live.diff"
cat > "$RELOC_LIVE" <<'DIFFEOF'
diff --git a/g.py b/g.py
index 3333333..4444444 100644
--- a/g.py
+++ b/g.py
@@ -40,3 +40,4 @@ def zulu():
     a1
     a2
+GATE_BYPASS
     a3
DIFFEOF
RELOC_ANCHOR="$(normalize_review_diff < "$RELOC_REVIEWED" | openssl dgst -sha256 | awk '{print $NF}')"
_reloc_r="$(patch_projection_review_diff < "$RELOC_REVIEWED" | openssl dgst -sha256 | awk '{print $NF}')"
_reloc_l="$(patch_projection_review_diff < "$RELOC_LIVE" | openssl dgst -sha256 | awk '{print $NF}')"
if [ "$_reloc_r" != "$_reloc_l" ]; then
    ok "(t20) the projection DISTINGUISHES a relocation: same +/- bytes under \`def alpha()\` vs \`def zulu()\` project differently"
else
    bad "(t20) a relocation projects IDENTICALLY — the projection is position-blind again, so a force-push can move reviewed lines with no re-review"
fi
diff_marker "$STALE" "$RELOC_ANCHOR" > "$T/body-t8"
STUB_DIFF_FILE="$RELOC_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$RELOC_REVIEWED" run_gate "$T/body-t8"
assert_rc 1 "(t20) the RELOCATED change is REFUSED end to end (anchor reproduces, patch differs)"
# The other direction, on the same pair: the reviewed diff ITSELF still carries, so the
# refusal above is the relocation and not a broken fixture.
STUB_DIFF_FILE="$RELOC_REVIEWED" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$RELOC_REVIEWED" run_gate "$T/body-t8"
assert_rc 0 "(t20) and the UNRELOCATED change still carries — so the refusal above is the relocation, not a dead rule"


# ── (t22) A REWRITTEN BRANCH IS NOT A CARRY (fresh review of #4255, P1) ────
# ⛔ THE HOLE THIS CLOSES, MEASURED. The projection keeps the enclosing-scope heading,
# so a relocation into ANOTHER scope is refused ((t20)). But a relocation WITHIN one
# scope — the same `+`/`-` bytes, moved from after an early `return` to the end of the
# same function — projects IDENTICALLY, and nothing else in rule (c) related the
# reviewed sha to the head. Measured end to end on a bare force-push, with NO base
# advance at all: `rc=0`, `passed via PATCH match`. The ANCHOR reproduced perfectly,
# which is why the header's old claim that the residual was "bounded by the anchor"
# was FALSE — the anchor proves where the REVIEWED bytes came from and says nothing
# about the live branch.
#
# THE FIX IS REACHABILITY, NOT ANOTHER DIGEST: `compare/<marker sha>...<head>` answers
# `ahead` only when the head DESCENDS from the reviewed commit. A base advance by merge
# ("Update branch") keeps that true — that is the carry this rule exists for. A
# force-push / rebase / amend rewrites the commit out of the history and answers
# `diverged`, so rule (c) refuses and the verdict cannot ride a move.
SAME_SCOPE_REVIEWED="$T/same-scope-reviewed.diff"
cat > "$SAME_SCOPE_REVIEWED" <<'DIFFEOF'
diff --git a/g.py b/g.py
index 1111111..2222222 100644
--- a/g.py
+++ b/g.py
@@ -3,4 +3,6 @@ def alpha(x):
     a1
+    if x: return -1
+    danger = os.system("rm -rf /tmp/x")
     a2
     a3
DIFFEOF
SAME_SCOPE_LIVE="$T/same-scope-live.diff"
cat > "$SAME_SCOPE_LIVE" <<'DIFFEOF'
diff --git a/g.py b/g.py
index 5555555..6666666 100644
--- a/g.py
+++ b/g.py
@@ -3,4 +3,6 @@ def alpha(x):
+    if x: return -1
     a1
     a2
     a3
+    danger = os.system("rm -rf /tmp/x")
DIFFEOF
SAME_SCOPE_ANCHOR="$(normalize_review_diff < "$SAME_SCOPE_REVIEWED" | openssl dgst -sha256 | awk '{print $NF}')"
_ss_r="$(patch_projection_review_diff < "$SAME_SCOPE_REVIEWED" | openssl dgst -sha256 | awk '{print $NF}')"
_ss_l="$(patch_projection_review_diff < "$SAME_SCOPE_LIVE" | openssl dgst -sha256 | awk '{print $NF}')"
if [ "$_ss_r" = "$_ss_l" ]; then
    ok "(t22) the fixture reproduces the blind spot: a SAME-SCOPE relocation projects identically (so the refusal below is the ancestry guard, not the projection)"
else
    bad "(t22) the same-scope fixture no longer projects identically — this case would pass for the WRONG reason, so the ancestry guard below is unattributed"
fi
# (a) The HARM the guard exists for: rewritten history, patch equal, anchor reproducible.
diff_marker "$STALE" "$SAME_SCOPE_ANCHOR" > "$T/body-t22"
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" STUB_ANCESTOR_STATUS=diverged run_gate "$T/body-t22"
assert_rc 1 "(t22) a FORCE-PUSHED branch does NOT carry a review, even though the patch projection matches (the ANCESTRY precondition)"
assert_not_contains "(t22) …and no patch match is claimed" "passed via PATCH match"
assert_contains "(t22) …and the operator is told the branch was REWRITTEN, not handed the generic stale-diff message" \
    "is NOT an ancestor of this PR's head"
if grep -qE -- "repos/${REPO_NAME}/compare/${STALE}\.\.\.${HEAD}" "$T/gh.log"; then
    ok "(t22) the ancestry question was asked as compare/<marker sha>...<head> (the discriminator is reachability, not another digest)"
else
    bad "(t22) the gate never asked whether the reviewed commit is reachable from the head (stub log: $(cat "$T/gh.log"))"
fi
# (a2) …and THAT diagnosis must survive an unrelated decoy (§(t27)'s fixture). The guard
# that prints it used to be `[ -z "$candidates" ]`, which meant "the arm appended
# nothing" only while the arm could not run with decoys present; once it can (§(t27)),
# one pasted marker-shaped line silenced every rule-(c) warning and left the operator
# with the HMAC-mismatch advice — i.e. told to fix a key that was fine.
{ printf 'review recorded: reviews/%s.json verdict=clean @ %s (%s) sig=%s\n' \
      "$PR_NUMBER" "$HEAD" "$REPO_NAME" "$(printf '0%.0s' $(seq 1 64))"; \
  diff_marker "$STALE" "$SAME_SCOPE_ANCHOR"; } > "$T/body-t22-decoy"
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" STUB_ANCESTOR_STATUS=diverged \
    run_gate "$T/body-t22-decoy"
assert_rc 1 "(t22) a decoy line beside a REWRITTEN branch still FAILS CLOSED"
assert_contains "(t22) …and the operator is STILL told the branch was rewritten (a pasted line does not silence the diagnosis)" \
    "is NOT an ancestor of this PR's head"
# ⛔ A ROW WAS DELETED HERE (final review). It asserted that the REWRITE diagnosis
# precedes the HMAC-mismatch line in a decoy run — but the rewrite warning was never
# one of the two blocks the hoist moved (`git log -S 'is NOT an ancestor of this PR'`
# dates it three commits earlier), so the row passed on the pre-hoist code and pinned
# nothing. Worse, both line probes were `grep | head | cut` assignments with no
# `|| true` under this file's `set -euo pipefail`, so the population the row existed
# for — the diagnosis ABSENT — killed the harness at that line instead of reporting
# it. The ORDER property is pinned in `(t10b)` instead, against the blocks that
# actually moved.
# (b) The other direction on the SAME bytes: an ADVANCED branch (the head descends from
# the reviewed commit) still carries, so the refusal above is the rewrite and not a
# rule that refuses everything. Without this half, deleting rule (c) would look green.
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" run_gate "$T/body-t22"
assert_rc 0 "(t22) the SAME bytes on an ADVANCED branch still carry — so the refusal above is the rewrite, not a dead rule"
# (c) MUTATION: delete the ancestry precondition and (t22) must PASS. This is what
# makes the guard load-bearing rather than decoration.
MUT_ANC="$T/mutant-no-ancestry.sh"
sed 's/^  if \[ "\$p_rel" != "ahead" \]; then patch_rewritten=1; return 1; fi$/  :/' "$RUN_BLOCK" > "$MUT_ANC"
if cmp -s "$RUN_BLOCK" "$MUT_ANC"; then
    bad "(t22) the ancestry mutation did not apply — the guard line changed shape, so this control proves NOTHING"
else
    _saved_block="$RUN_BLOCK"; RUN_BLOCK="$MUT_ANC"
    STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
        STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" STUB_ANCESTOR_STATUS=diverged run_gate "$T/body-t22"
    RUN_BLOCK="$_saved_block"
    if [ "$GATE_RC" = "0" ]; then
        ok "(t22) the MUTANT carries the force-push — so the ancestry precondition is what refuses it"
    else
        bad "(t22) the MUTANT still refused (rc=$GATE_RC) — (t22) does not depend on the ancestry precondition"
    fi
fi


# ── (t23) A NEW AUTHORED COMMIT IS NOT A CARRY EITHER (review cycle 2, P1) ──
# ⛔ THE HOLE (t22) DID NOT CLOSE. Reachability alone is satisfied by ANY commit on
# top of the reviewed sha — `git commit` needs no force-push. Measured by the cycle-2
# review: reviewed `if not ok: return` then `+audit()`, live `+audit()` then
# `if not ok: return` — the same `+`/`-` lines relocated ACROSS an existing guard
# (so `audit()` now runs when it must not), same hunk frame `@@ def f(ok):`, PATCH
# identical, BINDING different (so rules (a)/(b) cannot carry it) — and the gate at
# 85bde797 returned `rc=0 :: passed via PATCH match`. The branch had merely ADVANCED,
# so the reachability precondition was satisfied and rule (c) carried a change the
# reviewer never approved.
#
# The guard is now the shape of what was added: everything the branch gained after the
# reviewed sha must be a MERGE commit. A base advance does exactly that; an authored
# change does not. `NEWC` is a head-side commit the branch added after the review.
NEWC="$(printf 'd%.0s' $(seq 1 12))"
RELOCATED_MARKER="$(diff_marker "$STALE" "$SAME_SCOPE_ANCHOR")"   # anchor reproduces; patch would match
printf '%s\n' "$RELOCATED_MARKER" > "$T/body-t23"
# (a) A PLAIN COMMIT that relocates the reviewed lines: REFUSED.
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" \
    STUB_ANCESTOR_STATUS=ahead STUB_ANCESTOR_TOTAL=1 STUB_ANCESTOR_SEEN=1 \
    STUB_ANCESTOR_COMMITS="${NEWC}:1" STUB_BRANCH_SHAS="$NEWC" \
    run_gate "$T/body-t23"
assert_rc 1 "(t23) a NON-MERGE commit pushed after the review does NOT carry a review, even with the patch projection identical"
assert_not_contains "(t23) …and no patch match is claimed" "passed via PATCH match"
assert_contains "(t23) …and the operator is told the branch gained an authored commit" "NON-MERGE commit"
# (b) THE SAME COMMIT AS A MERGE (2 parents) — this is the legal base-advance shape,
# so the refusal above is the commit's SHAPE and not a rule that refuses everything.
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" \
    STUB_ANCESTOR_STATUS=ahead STUB_ANCESTOR_TOTAL=1 STUB_ANCESTOR_SEEN=1 \
    STUB_ANCESTOR_COMMITS="${NEWC}:2" STUB_BRANCH_SHAS="$NEWC" \
    run_gate "$T/body-t23"
assert_rc 0 "(t23) the SAME commit as a MERGE carries — so (a) refuses the authored-commit shape, not the carry itself"
# (c) A COMMIT THE BASE BROUGHT IN (not in the branch-side list) is not the author's:
# merging the base forward adds single-parent commits of main's, and those must NOT
# refuse the carry, or `gh pr update-branch` would never carry anything.
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" \
    STUB_ANCESTOR_STATUS=ahead STUB_ANCESTOR_TOTAL=1 STUB_ANCESTOR_SEEN=1 \
    STUB_ANCESTOR_COMMITS="${NEWC}:1" STUB_BRANCH_SHAS="eeeeeeeeeeee" \
    run_gate "$T/body-t23"
assert_rc 0 "(t23) a single-parent commit that came from the BASE does not refuse the carry (the merge-forward shape still works)"
# (d) TRUNCATION IS UNVERIFIABLE, NOT A PASS: a page that claims more commits than it
# returned could hide the authored one.
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" \
    STUB_ANCESTOR_STATUS=ahead STUB_ANCESTOR_TOTAL=9 STUB_ANCESTOR_SEEN=1 \
    STUB_ANCESTOR_COMMITS="${NEWC}:2" STUB_BRANCH_SHAS="$NEWC" \
    run_gate "$T/body-t23"
assert_rc 1 "(t23) a TRUNCATED commit list refuses (unverifiable is never a pass)"
assert_contains "(t23) …and says the graph could not be READ, not that the branch was rewritten" "could not READ this PR's commit graph"

# (e) MUTATION: delete the authored-commit guard and (a) must CARRY. Without this the
# row is a behavioural pair, not an attribution — and a "both mutation-controlled"
# sentence in the docs would be false (the final review measured exactly that).
MUT_NEWC="$T/mutant-no-newcommit.sh"
sed 's/^      \[ "${p_np:-0}" -ge 2 \] || { patch_newcommit=1; return 1; }$/      :/' "$RUN_BLOCK" > "$MUT_NEWC"
if cmp -s "$RUN_BLOCK" "$MUT_NEWC"; then
    bad "(t23) the authored-commit mutation did not apply — the guard line changed shape, so this control proves NOTHING"
else
    _saved_block="$RUN_BLOCK"; RUN_BLOCK="$MUT_NEWC"
    STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
        STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" \
        STUB_ANCESTOR_STATUS=ahead STUB_ANCESTOR_TOTAL=1 STUB_ANCESTOR_SEEN=1 \
        STUB_ANCESTOR_COMMITS="${NEWC}:1" STUB_BRANCH_SHAS="$NEWC" \
        run_gate "$T/body-t23"
    RUN_BLOCK="$_saved_block"
    if [ "$GATE_RC" = "0" ]; then
        ok "(t23) the MUTANT carries the authored commit — so the shape check is what refuses it"
    else
        bad "(t23) the MUTANT still refused (rc=$GATE_RC) — (t23) does not depend on the shape check"
    fi
fi


# (e) BRANCH-SIDE TRUNCATION, the mirror of (d): if the branch-side page omits a sha,
# an AUTHORED commit would look like one the base brought in — the exact failure this
# guard exists to prevent — so a short page must refuse, not pass. This row is what
# makes the branch-side guard observable: without it, reverting it to the old
# `<= 250` test left the whole suite green (measured by the next review).
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" \
    STUB_ANCESTOR_STATUS=ahead STUB_ANCESTOR_TOTAL=1 STUB_ANCESTOR_SEEN=1 \
    STUB_ANCESTOR_COMMITS="${NEWC}:2" STUB_BRANCH_TOTAL=9 STUB_BRANCH_COUNT=1 \
    STUB_BRANCH_SHAS="$NEWC" \
    run_gate "$T/body-t23"
assert_rc 1 "(t23) a TRUNCATED BRANCH-SIDE list refuses too (a missing sha must never read as 'came from the base')"
assert_contains "(t23) …with the same 'could not READ' diagnosis" "could not READ this PR's commit graph"


# ── (t24) AN UNREADABLE ANCESTRY ANSWER IS NOT A REWRITE (review cycle 2, P2) ──
# A 502 on the compare call used to fall into the same branch as `diverged` and print
# "the branch was REWRITTEN (force-push / rebase / amend)" — a false statement about
# the operator's own PR whose advice is a 20–40 minute re-review for an API blip. The
# refusal stays (fail-closed); the DIAGNOSIS now distinguishes the two.
printf '%s\n' "$RELOCATED_MARKER" > "$T/body-t24"
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" STUB_COMPARE_JSON_FAIL=1 \
    run_gate "$T/body-t24"
assert_rc 1 "(t24) an UNREADABLE ancestry answer still refuses (fail-closed)"
assert_contains "(t24) …and tells the operator the read FAILED and to re-run" "re-run this job before re-recording"
assert_not_contains "(t24) …and does NOT claim the branch was rewritten" "REWRITTEN (force-push"

# ── (t25) THE PATCH-MISMATCH REFUSAL IS AUDIBLE (review cycle 2, P2) ──────────
# The docs tell an operator that a base advance rewriting the hunk's enclosing-scope
# heading is a DECLARED over-block they can recognise in the log. Before this row the
# gate printed NOTHING for it — the log was byte-identical to a genuine missing review,
# so the documented over-block sent the operator hunting a "bug in this gate".
printf '%s\n' "$(diff_marker "$STALE" "$PB_REVIEWED")" > "$T/body-t25"
STUB_DIFF_FILE="$RELOC_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t25"
assert_rc 1 "(t25) an authored change (or a heading rewrite) still refuses"
assert_contains "(t25) …and the log SAYS the anchor held but the projection differs, naming the declared over-block" "the ANCHOR held"


# ── (t20b) THE FRAME IS A STRING, NOT A POSITION — the DECLARED residual ─────
# ⛔ THE LIMIT THIS PINS, MEASURED. `(t20)` refuses a relocation between `def alpha()`
# and `def zulu()` — DIFFERENT heading text. Two DISTINCT scopes that share the SAME
# text (two `def handler(event):` at column 0, two duplicate markdown headings, two
# letter-initial lines in a driverless text file) render the SAME frame, so a
# relocation between them projects IDENTICALLY.
#
# ⛔ AND A FIRST VERSION OF THIS ROW WAS VACUOUS — measured by the next review. It used
# a pair differing ONLY in the hunk's line numbers, and the BINDING normalizer rewrites
# `@@ -a,b +c,d @@` to `@@ -0,b +0,d @@`, so the two sides normalized to the SAME digest
# and the carry came from rule (b) (`passed via diff match`) with rule (c) never
# consulted — the row passed with the patch arm entirely disabled. The fixtures below
# therefore ALSO differ in a CONTEXT line (which the binding KEEPS and the patch DROPS),
# so the binding moves and rule (c) is genuinely the arm under test.
#
# What actually bounds the residual is the two SHAPE checks, and rows (a)/(b)/(c) below
# prove they do: an ADVANCED branch carries, and a rewritten branch or an authored commit
# after the review is refused.
DUP_SCOPE_REVIEWED="$T/dup-scope-reviewed.diff"
cat > "$DUP_SCOPE_REVIEWED" <<'DIFFEOF'
diff --git a/h.py b/h.py
index 1111111..2222222 100644
--- a/h.py
+++ b/h.py
@@ -2,4 +2,5 @@ def handler(event):
     a1
     a2
+AUDIT()
     a3
     a4
DIFFEOF
# ⛔ THE PAIR MUST DIFFER IN CONTEXT, NOT ONLY IN LINE NUMBERS. A first version of this
# row differed only in the `@@ -a,b` numbers, which the BINDING normalizer rewrites to
# `@@ -0,b …` — so both sides normalized to the SAME digest, rule (b) carried it
# (`passed via diff match`), rule (c) was never consulted, and the row stayed green with
# the patch arm entirely disabled (measured by the next review). The context line
# `a4` -> `a9` is KEPT by the binding and DROPPED by the patch projection, which is
# exactly the split this section needs.
DUP_SCOPE_LIVE="$T/dup-scope-live.diff"
cat > "$DUP_SCOPE_LIVE" <<'DIFFEOF'
diff --git a/h.py b/h.py
index 3333333..4444444 100644
--- a/h.py
+++ b/h.py
@@ -40,4 +40,5 @@ def handler(event):
     a1
     a2
+AUDIT()
     a3
     a9
DIFFEOF
DUP_SCOPE_ANCHOR="$(normalize_review_diff < "$DUP_SCOPE_REVIEWED" | openssl dgst -sha256 | awk '{print $NF}')"
_dup_r="$(patch_projection_review_diff < "$DUP_SCOPE_REVIEWED" | openssl dgst -sha256 | awk '{print $NF}')"
_dup_l="$(patch_projection_review_diff < "$DUP_SCOPE_LIVE" | openssl dgst -sha256 | awk '{print $NF}')"
if [ "$_dup_r" = "$_dup_l" ]; then
    ok "(t20b) DECLARED RESIDUAL: two scopes sharing one heading TEXT project identically, so a relocation between them is not distinguished by the projection"
else
    bad "(t20b) the two same-text scopes now project DIFFERENTLY — the residual is gone; update the docs bullet that declares it (do not leave them claiming a limit the code no longer has)"
fi
# The binding MUST move, or rule (b) answers first and this section tests nothing.
_dup_bn_r="$(normalize_review_diff < "$DUP_SCOPE_REVIEWED" | openssl dgst -sha256 | awk '{print $NF}')"
_dup_bn_l="$(normalize_review_diff < "$DUP_SCOPE_LIVE" | openssl dgst -sha256 | awk '{print $NF}')"
if [ "$_dup_bn_r" != "$_dup_bn_l" ]; then
    ok "(t20b) …and the BINDING moves on the same pair, so rule (b) cannot carry it and rule (c) is the arm under test"
else
    bad "(t20b) the binding is UNCHANGED on this pair, so rule (b) would answer first — this row would pass with the patch arm disabled (that was the first version of it)"
fi
printf '%s\n' "$(diff_marker "$STALE" "$DUP_SCOPE_ANCHOR")" > "$T/body-t20b"
# (a) The DECLARED residual, asserted as such: on an ADVANCED branch it carries.
STUB_DIFF_FILE="$DUP_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$DUP_SCOPE_REVIEWED" run_gate "$T/body-t20b"
assert_rc 0 "(t20b) …and on a branch that merely ADVANCED it CARRIES — the residual is real and is declared, not hidden"
assert_contains "(t20b) …via the PATCH arm (not rule (b), which the binding change already excluded)" "passed via PATCH match"
# (b) …and it is BOUNDED: a rewritten branch is refused, so the shape checks really do
# reach this sub-class (the claim the docs make).
STUB_DIFF_FILE="$DUP_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$DUP_SCOPE_REVIEWED" STUB_ANCESTOR_STATUS=diverged run_gate "$T/body-t20b"
assert_rc 1 "(t20b) …and a REWRITTEN branch is refused, so the residual is bounded by the shape checks after all"
# (c) …and an authored commit after the review is refused too.
STUB_DIFF_FILE="$DUP_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$DUP_SCOPE_REVIEWED" \
    STUB_ANCESTOR_STATUS=ahead STUB_ANCESTOR_TOTAL=1 STUB_ANCESTOR_SEEN=1 \
    STUB_ANCESTOR_COMMITS="ffffffffffff:1" STUB_BRANCH_SHAS="ffffffffffff" run_gate "$T/body-t20b"
assert_rc 1 "(t20b) …and an authored commit after the review is refused"


# ── (t29) AN EMPTY PROJECTION NEVER CARRIES (the sha256("") class) ───────────
# ⛔ THE ONE FALSE PASS THE MUTATION SWEEP FOUND UNPINNED. `sha256("")` is the CONSTANT
# `e3b0c442…`, which satisfies every 64-hex shape check — so if BOTH projections came out
# empty they would compare EQUAL and the arm would carry. TWO guards prevent it (the live
# side's `[ -s "$live_patch_tmp" ]` and the reviewed side's `[ -s "$p_patch_fn_out" ]`),
# and neither was individually pinned: removing ONE is invisible because the other
# refuses (measured — the single-guard mutants redden nothing). So this section pins the
# CLASS in both directions: with the guards in place the empty projection is REFUSED, and
# with both removed it CARRIES — which is the false pass they exist to stop.
_pp_empty_mutant() { # <dst> [strip-guards]
    python3 - "$RUN_BLOCK" "$1" "${2:-0}" <<'PYEOF'
import re, sys
src, dst, strip = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
text = open(src).read()
lines = text.split("\n")
out, skip = [], False
for line in lines:
    if line.startswith("patch_projection_review_diff() {"):
        out.append("patch_projection_review_diff() { cat >/dev/null; }")
        skip = True
        continue
    if skip:
        if line == "}":
            skip = False
        continue
    if strip:
        line = re.sub(r'^[ ]*&& \[ -s "\$live_patch_tmp" \]; then$', '    && true; then', line)
        line = re.sub(r'^[ ]*if \[ ! -s "\$p_patch_fn_out" \]; then rm -f "\$p_rt" "\$p_patch_fn_out"; return 1; fi$', '  :', line)
    out.append(line)
open(dst, "w").write("\n".join(out))
PYEOF
}
MUT_PP_EMPTY="$T/mutant-empty-projection.sh"
_pp_empty_mutant "$MUT_PP_EMPTY" 0
if cmp -s "$RUN_BLOCK" "$MUT_PP_EMPTY"; then
    bad "(t29) the empty-projection mutation did not apply — the projector's shape changed, so this control proves NOTHING"
else
    _saved_block="$RUN_BLOCK"; RUN_BLOCK="$MUT_PP_EMPTY"
    STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
        STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t1"
    RUN_BLOCK="$_saved_block"
    if [ "$GATE_RC" = "0" ]; then
        bad "(t29) an EMPTY projection CARRIED — sha256(\"\") matched itself into a pass"
    else
        ok "(t29) a projection that emits nothing is REFUSED (sha256(\"\") cannot become evidence)"
    fi
fi
# …and the two guards are what refuse it: remove BOTH and the same fixtures carry.
MUT_PP_EMPTY2="$T/mutant-empty-projection-unguarded.sh"
_pp_empty_mutant "$MUT_PP_EMPTY2" 1
if cmp -s "$MUT_PP_EMPTY" "$MUT_PP_EMPTY2"; then
    bad "(t29) the guard-stripping mutation did not apply — so the CARRIES half below proves nothing"
else
    _saved_block="$RUN_BLOCK"; RUN_BLOCK="$MUT_PP_EMPTY2"
    STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
        STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t1"
    RUN_BLOCK="$_saved_block"
    if [ "$GATE_RC" = "0" ]; then
        ok "(t29) with BOTH non-empty guards removed the empty projections match and the gate CARRIES — so those two guards are what stop this false pass"
    else
        bad "(t29) even with both guards removed the gate refused (rc=$GATE_RC) — something else refuses this, so the empty-projection hazard is not what the guards address"
    fi
fi


# ── (t31) NO LIVE PATCH HASH ⇒ NO PROBE, NO MISDIAGNOSIS ────────────────────
# ⛔ THE THIRD UNPINNED GUARD (final review): `[ -n "$live_patch_hash" ] || return 1`.
# Removing it reddened NOTHING, yet it is what stops a TRANSIENT live-diff failure from
# (a) spending a rule-(c) probe — 3 `gh` calls become 6 against the shared 1,000/hour
# budget — and (b) printing "the ANCHOR held … a fresh review IS owed", i.e. sending the
# operator to buy a 20–40 minute review for an API blip. That is the exact misdiagnosis
# the `patch_ancestry_unreadable` arm was added to prevent, one guard away.
printf '%s\n' "$(diff_marker "$STALE" "$SAME_SCOPE_ANCHOR")" > "$T/body-t31"
cp "$T/gh.log" "$T/gh-t31-before" 2>/dev/null || : > "$T/gh-t31-before"
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" STUB_DIFF_FAIL=1 \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" run_gate "$T/body-t31"
assert_rc 1 "(t31) an unreadable LIVE diff still refuses"
assert_not_contains "(t31) …and does NOT tell the operator a fresh review is owed for an API blip" \
    "fresh review IS owed"
_t31_calls="$(wc -l < "$T/gh.log" | tr -d ' ')"
if [ "${_t31_calls:-99}" -le 3 ]; then
    ok "(t31) …and spends NO rule-(c) probe (${_t31_calls} gh call(s)) — the budget is not spent on a failure"
else
    bad "(t31) ${_t31_calls} gh calls with no live patch hash — the probe ran on a transient failure"
fi
# MUTATION: drop the guard and both properties break.
MUT_LPH="$T/mutant-no-live-patch-guard.sh"
sed -E 's/^( *)\[ -n "\$live_patch_hash" \] \|\| return 1$/\1:/' "$RUN_BLOCK" > "$MUT_LPH"
if cmp -s "$RUN_BLOCK" "$MUT_LPH"; then
    bad "(t31) the live-patch-guard mutation did not apply — this control proves NOTHING"
else
    _saved_block="$RUN_BLOCK"; RUN_BLOCK="$MUT_LPH"
    STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" STUB_DIFF_FAIL=1 \
        STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" run_gate "$T/body-t31"
    RUN_BLOCK="$_saved_block"
    _mut_calls="$(wc -l < "$T/gh.log" | tr -d ' ')"
    if [ "${_mut_calls:-0}" -gt 3 ] || grep -q "fresh review IS owed" <<<"$GATE_OUT"; then
        ok "(t31) the MUTANT probes anyway (${_mut_calls} calls) and misdiagnoses — so the guard is load-bearing"
    else
        bad "(t31) the mutant behaved identically — the two assertions above attribute nothing"
    fi
fi


# ── (t30) THE BRANCH-SIDE READ IS A GUARD, NOT A CONVENIENCE ────────────────
# ⛔ NOT AN UNPINNED PAIR ANY MORE, AND THE ATTRIBUTION IS PER-HALF (review cycle 3, which
# measured this sentence FALSE as written). `[ -z "$p_bside" ]` and the branch-side
# ARITY check are what make an UNREADABLE branch-side graph refuse instead of treating a
# missing sha as "the base brought it in". Measured at `eebfd924`, each removed ALONE:
#   · `[ -z "$p_bside" ]`          -> 0 red rows          (attribution-less on its own)
#   · the branch-side ARITY line   -> 3 red rows, `(t32e)` x3
# so "removing EITHER alone reddened NO row" is wrong: `(t32e)` — added by this same PR —
# pins the arity half by itself, and only the `[ -z "$p_bside" ]` half is attribution-less.
# Removing ONLY that pair does NOT let it carry (measured: it reddens `(t32e)` and nothing
# else), so the mutant below is NOT "the pair" — it sweeps FIVE guards together: the
# branch-side numeric presence, the count equality, the arity check, `[ -z "$p_bside" ]`
# and `[ "${p_btotal:-0}" -ge 1 ]`. A carry needs all of them gone, which is stated here
# because this row's own mutant is the only thing that exercises any of them jointly.
# `STUB_BRANCH_FAIL` (added above) fails only this arm, as gh really can.
BSIDE_BODY="$T/body-t30"
printf '%s\n' "$(diff_marker "$STALE" "$SAME_SCOPE_ANCHOR")" > "$BSIDE_BODY"
# (a) The read fails: REFUSED, with the read-failure diagnosis, not a rewrite.
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" \
    STUB_ANCESTOR_STATUS=ahead STUB_ANCESTOR_TOTAL=1 STUB_ANCESTOR_SEEN=1 \
    STUB_ANCESTOR_COMMITS="dddddddddddd:2" STUB_BRANCH_FAIL=1 run_gate "$BSIDE_BODY"
assert_rc 1 "(t30) an UNREADABLE branch-side graph refuses (a missing sha must never read as 'came from the base')"
assert_contains "(t30) …and says the graph could not be READ" "could not READ this PR's commit graph"
assert_not_contains "(t30) …and does NOT claim the branch was rewritten" "REWRITTEN (force-push"
# (b) The sweep below neutralises FOUR branch-side guards and the same authored-commit
# fixture CARRIES — the false pass those guards prevent. ⛔ IT IS NOT "the pair":
# `[ -z "$p_bside" ]` + the arity check alone does NOT carry (measured: `rc=1`, the only
# red is this row's own `bad`), and restoring `p_bside` into the sweep still refuses.
# ⛔ AND THE COUNT-EQUALITY `[ "$p_btotal" = "$p_bcount" ]` IS NOT PART OF THE SWEEP: it is
# INERT on this fixture (`STUB_BRANCH_FAIL=1` makes `p_bside` empty, so `read` yields
# `""`/`""` and `[ "" = "" ]` is true), so neutralising it here would let this row's
# printed verdict name a member it never tested. It IS pinned — alone, by `(t23)`'s
# truncated branch page (measured: removing it reddens `(t23)` and nothing here).
# ⛔ THE VERDICT NAMES WHAT THE MUTANT SWEPT, NOT A GLOSSARY CATEGORY. TWO of the four are
# ALSO individually pinned (the arity check -> `(t32e)`, `p_btotal >= 1` -> `(t33)`); the
# other two — the branch-side numeric presence and `[ -z "$p_bside" ]` — are SUBSUMED
# (0 red rows alone; see the workflow header). What this row uniquely shows is that the
# four are JOINTLY load-bearing for THIS fixture. Do not restate that as a count of
# pinned members: an earlier version said "three", which double-counted the arity check.
MUT_BSIDE="$T/mutant-no-bside-guards.sh"
# Separate `-e`s, each with NO capture group used in the replacement: an alternation
# across two patterns with different group counts silently mangles the replacement
# (measured: it appended the original line and left the block unparseable, which surfaced
# as a misleading `rc=2` instead of this row's own verdict).
_bt_guards=(
  'if [ -z "$p_bside" ]; then patch_ancestry_unreadable=1; return 1; fi'
  '[[ "$p_btotal" =~ ^[0-9]+$ && "$p_bcount" =~ ^[0-9]+$ ]] || { patch_ancestry_unreadable=1; return 1; }'
  '[ "$(printf '"'"'%s'"'"' "$p_bshas" | wc -w)" = "$p_bcount" ] || { patch_ancestry_unreadable=1; return 1; }'
  '[ "${p_btotal:-0}" -ge 1 ] || { patch_ancestry_unreadable=1; return 1; }'
)
sed -E -e 's/^ *(if \[ -z "\$p_bside" \]; then patch_ancestry_unreadable=1; return 1; fi)$/:/' \
       -e 's/^ *(\[\[ "\$p_btotal" =~ \^\[0-9\]\+\$ && "\$p_bcount" =~ \^\[0-9\]\+\$ \]\] \|\| \{ patch_ancestry_unreadable=1; return 1; \})$/:/' \
       -e 's/^ *(\[ "\$\(printf .%s. "\$p_bshas" \| wc -w\)" = "\$p_bcount" \] \|\| \{ patch_ancestry_unreadable=1; return 1; \})$/:/' \
       -e 's/^( *).*p_btotal:-0.*-ge 1.*$/\1:/' \
    "$RUN_BLOCK" > "$MUT_BSIDE"
# ⛔ PER-PATTERN APPLICATION CHECK (#4255, review cycle 8). `cmp -s` only notices that
# NOTHING applied: if ONE pattern silently stops matching (a guard line changes shape),
# the mutant is the four-guard one, which CARRIES — so `cmp` passes, `bash -n` passes, the
# run carries, and the row would print a verdict about a set it did not sweep. Each of the
# four must therefore be GONE from the mutant; a survivor is reported by name, which is
# the same self-report `(t32h)` uses for its single guard.
# ⛔ TWO-SIDED, AND THE PRESENT HALF IS NOT OPTIONAL (#4255, review cycle 9). Asserting
# absence alone is blind to a guard that has been DELETED or reshaped in the gate: there is
# no literal left to find, so a three-guard mutant in a four-guard row reads as "all
# neutralised". Measured: deleting the branch-side numeric-presence guard left the suite
# GREEN and (t30) printing its four-guard verdict. Each literal must therefore be PRESENT
# in the pre-sweep block (it occurs exactly once) and ABSENT after it.
_bt_missing=0
for _g in "${_bt_guards[@]}"; do
    if ! grep -qF -- "$_g" "$RUN_BLOCK"; then
        bad "(t30) a guard this sweep claims to neutralise is not in the gate at all (deleted or reshaped), so the verdict below would name a set the mutant did not sweep: $_g"
        _bt_missing=1
    elif grep -qF -- "$_g" "$MUT_BSIDE"; then
        bad "(t30) a guard in the sweep was NOT neutralised, so the verdict below would name a set the mutant did not sweep: $_g"
        _bt_missing=1
    fi
done
if cmp -s "$RUN_BLOCK" "$MUT_BSIDE"; then
    bad "(t30) the branch-side guard mutation did not apply — the guards changed shape, so this control proves NOTHING"
elif ! bash -n "$MUT_BSIDE"; then
    bad "(t30) the branch-side mutant does not PARSE — an unparseable mutant cannot attribute anything (fix the mutation, not the row)"
elif [ "$_bt_missing" -eq 0 ]; then
    _saved_block="$RUN_BLOCK"; RUN_BLOCK="$MUT_BSIDE"
    STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
        STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" \
        STUB_ANCESTOR_STATUS=ahead STUB_ANCESTOR_TOTAL=1 STUB_ANCESTOR_SEEN=1 \
        STUB_ANCESTOR_COMMITS="dddddddddddd:1" STUB_BRANCH_FAIL=1 run_gate "$BSIDE_BODY"
    RUN_BLOCK="$_saved_block"
    if [ "$GATE_RC" = "0" ]; then
        ok "(t30) with those FOUR branch-side guards neutralised an API failure CARRIES an authored commit — so they are jointly load-bearing for this fixture (the count-equality guard is inert here and is pinned by (t23) instead)"
    else
        bad "(t30) the four-guard mutant still refused (rc=$GATE_RC) — something else refuses this, so the row's claim about that set is wrong"
    fi
fi


# ── (t32) A DIFFERENTLY-SHAPED GRAPH ROW IS UNREADABLE, NEVER "CONSISTENT" ──
# ⛔ THE FOURTH UNPINNED REGION (final review of #4255, 2026-11-28). The reviewed-side
# counts used to DEFAULT to `0`, so a row that rendered FEWER fields read as a consistent
# page (`0 == 0`) and — `p_added` being empty too — the branch-side read AND the
# authored-commit loop were skipped SILENTLY. Rule (c) then reduced to `status=ahead`
# alone, i.e. a post-review AUTHORED commit that relocates the reviewed `+`/`-` lines
# inside one scope carries: the measured false pass (t23) this whole guard exists to stop.
# It was invisible because the stub RENDERS the row itself, so a changed `--jq` expression
# cannot be told from a changed fixture — measured: swapping it for `.status | @tsv` left
# the suite green. `STUB_ANCESTOR_ROW` (added above) serves a literal
# row, which is how a differently-shaped answer is reproduced here.
T32_BODY="$T/body-t32"
printf '%s\n' "$(diff_marker "$STALE" "$SAME_SCOPE_ANCHOR")" > "$T32_BODY"
# ── (t32a) status ONLY — the exact row a `.status | @tsv` expression produces.
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" STUB_ANCESTOR_ROW="ahead" run_gate "$T32_BODY"
assert_rc 1 "(t32a) a SHORT graph row (status only) REFUSES — absent counts are not a consistent page"
assert_contains "(t32a) …and names it a READ failure" "could not READ this PR's commit graph"
assert_not_contains "(t32a) …and does NOT claim the branch was rewritten" "REWRITTEN (force-push"
# ── (t32b) counts present but EMPTY — the same defect one shape over.
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" STUB_ANCESTOR_ROW="$(printf 'ahead\t\t')" run_gate "$T32_BODY"
assert_rc 1 "(t32b) EMPTY counts refuse — an empty pair must not read as a consistent page"
assert_contains "(t32b) …and says the graph could not be READ" "could not READ this PR's commit graph"
# ── (t32c) A STATUS OUTSIDE THE ALLOWLIST IS UNREADABLE, NOT A REWRITE ────────
# ⛔ THE FIXTURE MATTERS (review cycle 2). The first version of this row fed a
# LEADING-TAB row (\t0\t0\t) to reproduce an absent status — but `IFS=<tab> read`
# strips LEADING IFS whitespace, so that row parses as p_rel="0", p_total="0",
# p_seen="" (measured). It was therefore refused by the ARITY guard, not by the
# allowlist, and deleting the allowlist left the suite green: the row named a
# direction it never exercised. This fixture keeps every other field valid — counts
# numeric and equal, list arity matching seen — so ONLY the allowlist can refuse it.
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" STUB_ANCESTOR_ROW="$(printf 'sideways\t1\t1\t%s:2' "$NEWC")" run_gate "$T32_BODY"
assert_rc 1 "(t32c) a status outside ahead/behind/diverged/identical REFUSES (the allowlist, not a later guard)"
assert_contains "(t32c) …as UNREADABLE, not as a rewrite" "could not READ this PR's commit graph"
assert_not_contains "(t32c) …so the operator is not sent to buy a 20-40 minute review for an API blip" "REWRITTEN (force-push"
# ── (t32d) THE COUNTS CAN AGREE AND THE LIST STILL BE MISSING (reviewed side) ──
# Measured by review cycle 2: `ahead\t1\t1\t` — counts present, numeric, EQUAL, sha
# list EMPTY — passed every check and then fell out of `[ -n "$p_added" ]`, skipping the
# branch-side read and the authored-commit loop silently (`rc=0`, `passed via PATCH
# match`, on an authored commit). `p_seen` must equal the list's arity.
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" STUB_ANCESTOR_ROW="$(printf 'ahead\t1\t1\t')" run_gate "$T32_BODY"
assert_rc 1 "(t32d) counts that AGREE with an EMPTY sha list refuse — a missing list must not skip the authored-commit guard"
assert_contains "(t32d) …and says the graph could not be READ" "could not READ this PR's commit graph"
assert_not_contains "(t32d) …and no patch match is claimed" "passed via PATCH match"
# ── (t32e) …AND THE SAME INVARIANT ON THE BRANCH SIDE ────────────────────────
# The dangerous shape: the branch gained a single-parent AUTHORED commit, and the
# branch-side sha list is EMPTY while its counts agree — so every token misses the
# membership test and the authored commit reads as "the base brought it in".
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" \
    STUB_ANCESTOR_STATUS=ahead STUB_ANCESTOR_TOTAL=1 STUB_ANCESTOR_SEEN=1 \
    STUB_ANCESTOR_COMMITS="${NEWC}:1" \
    STUB_BRANCH_TOTAL=1 STUB_BRANCH_COUNT=1 STUB_BRANCH_SHAS="" \
    run_gate "$T32_BODY"
assert_rc 1 "(t32e) an EMPTY branch-side sha list with agreeing counts REFUSES — it must never read as 'came from the base'"
assert_contains "(t32e) …and says the graph could not be READ" "could not READ this PR's commit graph"
assert_not_contains "(t32e) …and no patch match is claimed" "passed via PATCH match"
# ── (t32g) A PAGE THAT CONTRADICTS THE HEAD IT DESCRIBES ─────────────────────
# `ahead<TAB>0<TAB>0<TAB>` — the counts are present, numeric, equal, and arity-consistent
# (zero words for a count of zero), so every check (t32a)-(t32e) passes it. But the marker
# sha is ALREADY known to differ from the head, so a page claiming ZERO commits between
# them is self-contradictory: GitHub cannot render that. Left unchecked it skipped the
# authored-commit loop and a changed `--jq` expression rendering literal zeros would carry
# (review cycle 3).
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" STUB_ANCESTOR_ROW="$(printf 'ahead\t0\t0\t')" run_gate "$T32_BODY"
assert_rc 1 "(t32g) a page claiming ahead with ZERO commits between two different shas REFUSES"
assert_contains "(t32g) …as an UNREADABLE page" "could not READ this PR's commit graph"
assert_not_contains "(t32g) …and no patch match is claimed" "passed via PATCH match"
# ── (t32h) its own mutation control: strip ONLY the self-consistency guard.
MUT_T32H="$T/mutant-no-seen-guard.sh"
sed -E 's/^ *(\[ "\$p_seen" -ge 1 \] \|\| \{ patch_ancestry_unreadable=1; return 1; \})$/:/' "$RUN_BLOCK" > "$MUT_T32H"
if cmp -s "$RUN_BLOCK" "$MUT_T32H"; then
    bad "(t32h) the self-consistency mutation did not apply — this control proves NOTHING"
elif ! bash -n "$MUT_T32H"; then
    bad "(t32h) the mutant does not PARSE — it cannot attribute anything"
else
    _saved_block="$RUN_BLOCK"; RUN_BLOCK="$MUT_T32H"
    STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
        STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" STUB_ANCESTOR_ROW="$(printf 'ahead\t0\t0\t')" run_gate "$T32_BODY"
    RUN_BLOCK="$_saved_block"
    if [ "$GATE_RC" = "0" ]; then
        ok "(t32h) with the self-consistency guard removed that page CARRIES — so it is load-bearing"
    else
        bad "(t32h) the mutant still refused (rc=$GATE_RC) — (t32g) attributes nothing"
    fi
fi
# ── (t32f) MUTATION: strip the presence, equality, allowlist, list-arity AND
# self-consistency guards -> the short row CARRIES. ⛔ IT MUST STRIP ALL FIVE: each one
# alone is masked by a sibling, so a shorter strip set leaves this row refusing and the
# control would attribute nothing (measured: presence+arity only reddens `(t32d)`, because
# `[ "$p_seen" -ge 1 ]` still refuses on an empty count).
MUT_T32="$T/mutant-no-arity-guards.sh"
sed -E -e 's/^ *(\[\[ "\$p_rel" =~ \^\(ahead\|behind\|diverged\|identical\)\$ \]\] \|\| \{ patch_ancestry_unreadable=1; return 1; \})$/:/' \
       -e 's/^ *(\[\[ "\$p_total" =~ \^\[0-9\]\+\$ && "\$p_seen" =~ \^\[0-9\]\+\$ \]\] \|\| \{ patch_ancestry_unreadable=1; return 1; \})$/:/' \
       -e 's/^ *(\[ "\$p_total" = "\$p_seen" \] \|\| \{ patch_ancestry_unreadable=1; return 1; \})$/:/' \
       -e 's/^ *(\[ "\$\(printf .%s. "\$p_added" \| wc -w\)" = "\$p_seen" \] \|\| \{ patch_ancestry_unreadable=1; return 1; \})$/:/' \
       -e 's/^ *(\[ "\$p_seen" -ge 1 \] \|\| \{ patch_ancestry_unreadable=1; return 1; \})$/:/' \
    "$RUN_BLOCK" > "$MUT_T32"
if cmp -s "$RUN_BLOCK" "$MUT_T32"; then
    bad "(t32f) the guard-set mutation did not apply — this control proves NOTHING"
elif ! bash -n "$MUT_T32"; then
    bad "(t32f) the arity mutant does not PARSE — an unparseable mutant cannot attribute anything"
else
    _saved_block="$RUN_BLOCK"; RUN_BLOCK="$MUT_T32"
    STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
        STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" STUB_ANCESTOR_ROW="ahead" run_gate "$T32_BODY"
    RUN_BLOCK="$_saved_block"
    if [ "$GATE_RC" = "0" ]; then
        ok "(t32f) with the FIVE guards removed the SHORT row CARRIES — so the set is jointly load-bearing (each one alone is masked by a sibling)"
    else
        bad "(t32f) the mutant still refused (rc=$GATE_RC) — something else refuses this, so the rows above attribute nothing"
    fi
fi


# ── (t33) THE BRANCH-SIDE PAGE CANNOT CLAIM ZERO EITHER ──────────────────────
# ⛔ `(t32g)` pins the REVIEWED side's "ahead with zero commits is self-contradictory"
# guard. The branch side needed the same one and did not have it: that read only runs when
# the branch gained at least one commit (the merge that brought the base in is head-side
# and NOT in the base), so a `0`-total branch page is equally malformed — and with the
# guard absent, an authored commit read as "the base brought it in". Measured before the
# guard: `rc=0`, `passed via PATCH match`.
printf '%s\n' "$(diff_marker "$STALE" "$SAME_SCOPE_ANCHOR")" > "$T/body-t33"
STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" \
    STUB_ANCESTOR_STATUS=ahead STUB_ANCESTOR_TOTAL=1 STUB_ANCESTOR_SEEN=1 \
    STUB_ANCESTOR_COMMITS="dddddddddddd:1" \
    STUB_BRANCH_TOTAL=0 STUB_BRANCH_COUNT=0 STUB_BRANCH_SHAS="" run_gate "$T/body-t33"
assert_rc 1 "(t33) a branch-side page claiming ZERO commits while the branch gained one REFUSES"
assert_contains "(t33) …as an UNREADABLE page" "could not READ this PR's commit graph"
assert_not_contains "(t33) …and no patch match is claimed" "passed via PATCH match"
# MUTATION: delete the branch-side zero guard and the same fixture CARRIES an authored commit.
MUT_ZERO="$T/mutant-no-branch-zero-guard.sh"
sed -E 's/^( *).*p_btotal:-0.*-ge 1.*$/\1:/' "$RUN_BLOCK" > "$MUT_ZERO"
if cmp -s "$RUN_BLOCK" "$MUT_ZERO"; then
    bad "(t33) the branch-zero mutation did not apply — this control proves NOTHING"
else
    _saved_block="$RUN_BLOCK"; RUN_BLOCK="$MUT_ZERO"
    STUB_DIFF_FILE="$SAME_SCOPE_LIVE" STUB_BASE_SHA="$BASE_SHA" \
        STUB_COMPARE_FILE="$SAME_SCOPE_REVIEWED" \
        STUB_ANCESTOR_STATUS=ahead STUB_ANCESTOR_TOTAL=1 STUB_ANCESTOR_SEEN=1 \
        STUB_ANCESTOR_COMMITS="dddddddddddd:1" \
        STUB_BRANCH_TOTAL=0 STUB_BRANCH_COUNT=0 STUB_BRANCH_SHAS="" run_gate "$T/body-t33"
    RUN_BLOCK="$_saved_block"
    if [ "$GATE_RC" = "0" ]; then
        ok "(t33) with that guard removed the zero-page CARRIES an authored commit — so it is load-bearing"
    else
        bad "(t33) the mutant still refused (rc=$GATE_RC) — (t33) attributes nothing"
    fi
fi


# ── (t26) REPEATED RECORDS COST ONE PROBE — AND WHAT IS NOT ATTRIBUTABLE ─────
# The workflow header used to name four cases as pinning the two availability bounds;
# the cycle-2 review measured that deleting the SIGNATURE-FIRST check reddens (t9)x2,
# (t10)x2 and (t21) — not (t6)/(t8)/(t22) — and that deleting the in-loop (sha,diff)
# `case` reddens NOTHING. ⛔ The cycle-2 figure was ITSELF under-counted: re-measured
# 2026-11-28, the same mutant reddens NINE rows — (t9)x2, (t10)x2, (t10b)x4, (t21)x1 —
# because (t10b)'s four absence rows also depend on the signature-first order. The
# header now states the measured nine. This row pins the property that is reachable
# end to end:
# repeating one record costs ONE probe. ⛔ Which of the two dedups delivers it is NOT
# attributable from here, and that is stated rather than implied: `all_markers` is
# de-duplicated UPSTREAM on the whole line (`awk '!seen[$0]++'`) and filtered to the
# accepted marker shapes, so the in-loop `case` is unreachable defence in depth — a
# mutation of it alone leaves this green (measured 2026-11-28). It is kept as a belt,
# not counted as a pinned bound.
DUPBODY="$T/body-t26"
{ diff_marker "$STALE" "$PB_REVIEWED"; diff_marker "$STALE" "$PB_REVIEWED"; diff_marker "$STALE" "$PB_REVIEWED"; } > "$DUPBODY"
STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$DUPBODY"
assert_rc 0 "(t26) the same signed record repeated three times still carries"
_t26="$(reviewed_diff_compares "$T/gh.log")"
if [ "$_t26" -eq 1 ]; then
    ok "(t26) three copies of ONE record bought exactly 1 reviewed-diff compare"
else
    bad "(t26) three copies of one record bought $_t26 reviewed-diff compares — the dedup is gone"
fi


# ── (t21) AN UNSIGNED MARKER-SHAPED LINE BUYS NO API CALL (fresh review of #4255, P2) ──
# Rule (c) fetches `.base.sha` and a `compare` per CANDIDATE, and `$all_markers` is every
# line of the AUTHOR-WRITABLE PR body that merely MATCHES the marker regex. Measured by
# the fresh review: a body with 50 unsigned marker-shaped lines produced 103 gh calls; a
# 64 KiB body (the cap) fits ~257 such lines ≈ 514 calls inside a `timeout-minutes: 15`
# job, on the RED path, repeatable by anyone who can open a PR. The HMAC is now checked
# BEFORE the API, which removes no acceptance: a line whose signature does not verify can
# never be accepted by the check that follows.
_unsigned="$T/body-t9"
: > "$_unsigned"
for _i in $(seq 1 20); do
    printf 'review recorded: reviews/2982.json verdict=clean @ %s diff=%s (%s) sig=%s\n' \
        "$STALE" "$RELOC_ANCHOR" "$REPO_NAME" "$(printf '0%.0s' $(seq 1 64))" >> "$_unsigned"
done
STUB_DIFF_FILE="$RELOC_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$RELOC_REVIEWED" run_gate "$_unsigned"
assert_rc 1 "(t21) twenty unsigned marker-shaped lines still FAIL CLOSED"
_n_cmp="$(grep -cE -- '/compare/' "$T/gh.log" || true)"
if [ "$_n_cmp" -eq 0 ]; then
    ok "(t21) and bought ZERO compare calls — the signature is verified before the API, so an attacker-writable body cannot spend the job's budget"
else
    bad "(t21) made $_n_cmp compare call(s) for UNSIGNED lines — the patch arm is reachable from any marker-shaped line before its signature is checked"
fi

# ── (t27) A HEAD-BOUND LINE WHOSE `sig=` DOES NOT VERIFY MUST NOT SUPPRESS RULE (c) ─
# The gate reserves "UNSIGNED" for the sig-LESS case, which is never a candidate at all
# (`sha_ok_re` ends in ` sig=[0-9a-f]{64}$`) — so this decoy carries a 64-hex `sig=`
# that does not verify, which is the shape a reader can actually paste (a marker signed
# with a rotated key, one edited after signing, or one copied from another head).
# `candidates` is filled by SHAPE before any signature is checked, and the arm used to
# be gated on `-z "$candidates"`. One such line therefore disabled the whole arm and the
# run reported `HMAC mismatch`, sending the operator to buy a 20-40 minute re-review of
# a change nobody had touched. The guard now means "no ACCEPTABLE marker"; these rows
# pin both sides, and the decoy alone must still FAIL CLOSED.
{ printf 'review recorded: reviews/%s.json verdict=clean @ %s (%s) sig=%s\n' \
      "$PR_NUMBER" "$HEAD" "$REPO_NAME" "$(printf '0%.0s' $(seq 1 64))"; \
  diff_marker "$STALE" "$PB_REVIEWED"; } > "$T/body-t27"
STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t27"
assert_rc 0 "(t27) a signed stale marker still carries when the body ALSO holds a head-bound decoy whose sig= does not verify"
assert_contains "(t27) …naming the PATCH path (the arm ran)" "passed via PATCH match"
if grep -q 'HMAC mismatch' "$T/gate.out"; then
    bad "(t27) the run reported an HMAC mismatch although a SIGNED marker carried — the diagnosis blames the key for a decoy line"
else
    ok "(t27) …and no HMAC-mismatch diagnosis (which would have sent the operator to re-mint the key)"
fi
# …and the decoy ALONE must still FAIL CLOSED: the fix widens no acceptance.
printf 'review recorded: reviews/%s.json verdict=clean @ %s (%s) sig=%s\n' \
    "$PR_NUMBER" "$HEAD" "$REPO_NAME" "$(printf '0%.0s' $(seq 1 64))" > "$T/body-t27b"
STUB_DIFF_FILE="$LIVE_DIFF_FILE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t27b"
assert_rc 1 "(t27) the unverifiable head-bound decoy ALONE still fails closed"
assert_not_contains "(t27) …and claims no review" "passed via PATCH match"
# ⛔ AND THE APPEND SEPARATOR, which the guard above makes reachable: `candidates` is
# rebuilt by a command substitution (trailing newlines STRIPPED), so the pre-review
# `${candidates}${line}` glued the decoy and the carried marker into ONE unsignable
# line. That row is the one above (rc must be 0 with a decoy present); it reddens on the
# glued form, so the separator is load-bearing rather than cosmetic.

# ── (t28) THE DECLARED OVER-BLOCK AT A BASE ADVANCE THAT REWRITES THE HEADING ─
# (t25) proves the patch-mismatch refusal is AUDIBLE; this row proves the heading
# rewrite itself is the trigger, using the same live bytes as (t1) with ONLY the hunk
# heading changed. git derives that heading from the last preceding line matching its
# default funcname pattern (`^[A-Za-z_$]`, gitattributes(5)), so a `main` insertion of a
# `def`/key above the reviewed hunk REWRITES it and the carry is refused. §(u)'s fixture
# is indented on purpose to avoid exactly this, so the limit was measured and left
# unpinned until now — pinned in the direction that must hold (REFUSE, never accept).
HEAD_MOVED_LIVE="$T/live-heading-moved.diff"
sed 's/^@@ -8,6 +8,7 @@ l7$/@@ -8,6 +8,7 @@ def helper():/' "$LIVE_DIFF_FILE" > "$HEAD_MOVED_LIVE"
if cmp -s <(sed 's/^@@ -8,6 +8,7 @@ def helper():$/@@ -8,6 +8,7 @@ l7/' "$HEAD_MOVED_LIVE") "$LIVE_DIFF_FILE"; then
    ok "(t28) the heading-moved fixture differs from (t1)'s live diff ONLY in the hunk heading"
else
    bad "(t28) the heading-moved fixture changed more than the heading — the row would not attribute the refusal"
fi
_pp_hm="$(patch_projection_review_diff < "$HEAD_MOVED_LIVE" | openssl dgst -sha256 | awk '{print $NF}')"
if [ "$_pp_hm" != "$PP_REVIEWED" ]; then
    ok "(t28) …and the patch projection MOVES with the heading (so the heading is load-bearing, not decorative)"
else
    bad "(t28) the heading does not move the projection — the (t20) refusal must then be something else"
fi
unset _pp_hm
diff_marker "$STALE" "$PB_REVIEWED" > "$T/body-t28"
STUB_DIFF_FILE="$HEAD_MOVED_LIVE" STUB_BASE_SHA="$BASE_SHA" \
    STUB_COMPARE_FILE="$REVIEWED_DIFF_FILE" run_gate "$T/body-t28"
assert_rc 1 "(t28) a base advance that inserts a funcname-matching heading above the hunk REFUSES (the declared over-block)"
assert_not_contains "(t28) …and does not claim a patch match" "passed via PATCH match"
assert_contains "(t28) …and the log names the over-block instead of a silently-changed diff" "the ANCHOR held"

# ── The CROSS-REPO probe: does the LOCAL producer agree with this consumer? ──────
# The workflow header calls the patch projection a cross-repo contract with agent-infra's
# `scripts/lib/diff-normalize.py --patch-only`, and nothing in the fleet compares a
# producer digest with a consumer digest at runtime (the gate computes both sides
# itself; `record-review.sh`'s `patch_dimension_carry` computes BOTH sides with the
# producer). So drift between the two halves is invisible until someone recomputes the
# pinned literals — which is how the position-bearing frame could be quietly reverted by
# a helpful reader. This probe MEASURES the local producer when there is one and
# classifies a disagreement instead of believing it away:
#   agree                          → PASS: the contract holds on this box.
#   PRE-#4261 shape (no `@@`)      → SKIP, named: it cannot agree by construction, and
#                                    agent-infra#4261 is the land order.
#   producer FAILED (rc != 0)      → its OWN named non-pass: a crash also emits no `@@`,
#                                    so without this arm a broken producer would be
#                                    swallowed as the old shape — the drift most worth
#                                    seeing.
#   position-bearing but different → FAIL: the halves have genuinely drifted.
# ⛔ The rc is probed on EVERY input this section reads: a producer that crashes only on
# the reviewed fixture used to fall through to the DRIFT branch, whose message is the one
# thing that invites aligning the consumer to the producer.
# A SKIP is not a PASS and is never counted as one.
_pp_producer=""
for _cand in "${AGENT_INFRA_PATH:-}/scripts/lib/diff-normalize.py" \
             "$HOME/.pi/agent/scripts/lib/diff-normalize.py"; do
    if [ -n "$_cand" ] && [ -f "$_cand" ]; then _pp_producer="$_cand"; break; fi
done
if [ -z "$_pp_producer" ]; then
    echo "  ⏭  (t) no agent-infra producer on this box — cross-repo agreement NOT measured here (this is not a pass)"
else
    _pp_prod_digest="$(python3 "$_pp_producer" --patch-only < "$REVIEWED_DIFF_FILE" 2>/dev/null \
        | openssl dgst -sha256 | awk '{print $NF}' || true)"
    printf 'diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@ def f():\n a\n-b\n+c\n' > "$T/pp-shape.diff"
    _pp_frames="$(python3 "$_pp_producer" --patch-only < "$T/pp-shape.diff" 2>/dev/null \
        | grep -c '^@@' || true)"
    _pp_rc=0
    python3 "$_pp_producer" --patch-only < "$REVIEWED_DIFF_FILE" >/dev/null 2>&1 || _pp_rc=$?
    _pp_rc2=0
    python3 "$_pp_producer" --patch-only < "$T/pp-shape.diff" > "$T/pp-shape.out" 2>/dev/null || _pp_rc2=$?
    _pp_out=$(wc -c < "$T/pp-shape.out" 2>/dev/null || echo 0)
    # ⛔ AND THE DIGEST THAT IS ACTUALLY COMPARED BELOW. `_pp_out` byte-counts the
    # SYNTHETIC shape probe only; a producer that is silent on the REVIEWED fixture
    # still produced `sha256("")`, which would be reported as DRIFT — the one message
    # that invites aligning the consumer to a broken producer (final review, measured
    # with a stub producer that is empty for f.md and normal for the shape probe).
    # `wc -c` ALWAYS prints a count, so no `|| echo 0` — that appended a second 0 on the
    # failed-command path and made the comparison `[ "0|0" -gt 0 ]` (log noise,
    # "integer expression expected"). ⛔ BUT THE `|| true` MUST STAY: under
    # `set -euo pipefail` the pipeline's status is the PRODUCER's, so a producer that
    # crashes on this fixture aborts the whole harness AT THIS LINE — killing the
    # `LOCAL producer … FAILED` row below, which is the named non-pass the docs promise
    # for exactly that case (measured: pre-fix PASS=249, post-abort rc=1 with no summary).
    _pp_rev_out="$(python3 "$_pp_producer" --patch-only < "$REVIEWED_DIFF_FILE" 2>/dev/null | wc -c || true)"
    _pp_rev_out="${_pp_rev_out//[!0-9]/}"
    [ "${_pp_rev_out:-0}" -gt 0 ] || _pp_out=0
    if [ "$_pp_rc" -ne 0 ] || [ "$_pp_rc2" -ne 0 ]; then
        echo "  ⏭  (t) the LOCAL producer at $_pp_producer FAILED (rc=${_pp_rc}/${_pp_rc2}) — cross-repo agreement NOT measured (this is not a pass, NOT the pre-#4261 shape, and NOT drift)"
    elif [ "${_pp_out:-0}" -eq 0 ]; then
        # rc=0 with NO OUTPUT is not a shape, it is a broken producer: a zero-byte
        # projection is the `sha256("")` hazard the gate refuses one level up, and
        # reporting it as "the pre-#4261 shape" would hide exactly the drift this probe
        # exists to see. Named separately, and counted as neither a pass nor a skip of
        # the shape class.
        echo "  ⏭  (t) the LOCAL producer at $_pp_producer exited 0 but produced NO OUTPUT — cross-repo agreement NOT measured, and this is NOT the pre-#4261 shape (a zero-byte projection is the empty-digest hazard, not an old frame)"
    elif [ "$_pp_prod_digest" = "$PP_REVIEWED" ]; then
        ok "(t) the LOCAL producer ($_pp_producer) agrees with the consumer on the reviewed fixture — the cross-repo contract holds on this box"
    elif [ "${_pp_frames:-0}" -eq 0 ]; then
        echo "  ⏭  (t) producer at $_pp_producer is the PRE-#4261 shape (it drops the hunk frame): it cannot agree by construction, and agent-infra#4261 is the land order — agreement NOT claimed (this is not a pass)"
    else
        bad "(t) the LOCAL producer is position-bearing but disagrees with the consumer ($_pp_prod_digest vs $PP_REVIEWED) — the two halves of the contract have drifted"
    fi
fi
unset _cand _pp_producer _pp_prod_digest _pp_frames _pp_rc _pp_rc2 _pp_out

echo "  PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || { echo "  ❌ FAILURES — fix and re-run"; exit 1; }
echo "  ✅ all checks passed"
exit 0
