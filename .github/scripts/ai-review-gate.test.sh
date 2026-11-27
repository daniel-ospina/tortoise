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
# the branch merged forward. The digests are LITERALS produced by agent-infra's own
# `scripts/lib/diff-normalize.py` (the producer half), so this is also the CROSS-REPO
# CONTRACT VECTOR: if either side's projection changes, one of these two files reddens.
#
#   reviewed binding dfebaa86…   patch f7f1d204…
#   live     binding 4d4d1118…   patch f7f1d204…   ← binding MOVED, patch IDENTICAL
#   one byte edited  patch d747ddd2…               ← a fresh review IS owed
#   whitespace added patch 13c304e8…               ← a fresh review IS owed (anti-patch-id)
#   RENAMED file     patch 52810b48…               ← a fresh review IS owed (path is identity)
#   DELETED line     patch d6e0cf6a…               ← a fresh review IS owed
# The rename/delete pairs exist because the workflow header used to claim BOTH
# were "pinned by the harness" while no fixture carried either — a coverage
# claim the harness did not back. They are also checked to move AWAY from the
# reviewed digest, so neither pin can pass vacuously.
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
PB_REVIEWED="dfebaa86f270853c99b5f809b31f6f5621009e8e5a0f03d3a42597470a62420c"
PB_LIVE="4d4d1118333448a5c97de826680e7fdcc929e133b179001565c03b2c80583eca"
PP_REVIEWED="f7f1d204dfd1bcd120e2088c8e6ac3b8cf768e6bb29fac3f947417c6840c5388"
PP_EDITED="d747ddd2a35a20377203a59ed6137b6e692634d020507840dc9c5319d0aaad60"
PP_WS="13c304e8ebf5fe32a50f855cb2fc7385fc01703fa7a47e8f3a38758507d80d7a"
PP_RENAME="52810b4893cde4493c51aa292454a7dab213c4c10cb80b4c8dc7ae7b3d3f3224"
PP_DELETED="d6e0cf6a8dc8f2cba10969a5726d92092f923f10911a6c94fe8bc6d220f1464c"
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
# …and each of the four "the authored change moved" vectors must ALSO differ
# from the reviewed digest. A pin that agrees with PP_REVIEWED would make the
# claim above vacuous, which is the failure mode the header sentence is about.
for _case in "a byte edit:$PP_EDITED" "whitespace:$PP_WS" \
             "a rename:$PP_RENAME" "a deleted line:$PP_DELETED"; do
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
sed 's/^  \[ "$p_patch" = "$live_patch_hash" \] || return 1$/  return 1/' "$RUN_BLOCK" > "$MUT_PP"
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
sed 's/^if \[ -z "$candidates" \] && \[ -n "$all_markers" \]; then$/if [ -n "$all_markers" ]; then/' \
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
    sed -e 's/^if \[ -z "$candidates" \] && \[ -n "$all_markers" \]; then$/if [ -n "$all_markers" ]; then/' \
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
_t9_compares="$(grep -c -- "/compare/" "$T/gh.log" || true)"
if [ "$_t9_compares" -le 1 ]; then
    ok "(t9) the forged lines bought NO probe (${_t9_compares} compare call(s) — signature verified FIRST)"
else
    bad "(t9) the body's forged lines bought $_t9_compares compare calls — the probe fan-out is a function of PR TEXT"
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
    _t9_cap_compares="$(grep -c -- "/compare/" "$T/gh.log" || true)"
    if [ "$_t9_cap_compares" -eq "$CAP_PIN" ]; then
        ok "(t9) the probe cap holds (${_t9_cap_compares} compares for $((CAP_PIN + 3)) signed records, cap $CAP_PIN)"
    else
        bad "(t9) $_t9_cap_compares compare calls for $((CAP_PIN + 3)) signed records (cap is $CAP_PIN) — the bound is not enforced"
    fi
    assert_contains "(t9) …and the cap is reported, not silent" "stopped after ${CAP_PIN} distinct SIGNED patch probes"
else
    bad "(t9) could not read PATCH_PROBE_CAP from the run block — the bound is unassertable"
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
{
  for n in $(seq 1 40); do printf 'context line %02d\n' "$n"; done
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

echo "  PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || { echo "  ❌ FAILURES — fix and re-run"; exit 1; }
echo "  ✅ all checks passed"
exit 0
