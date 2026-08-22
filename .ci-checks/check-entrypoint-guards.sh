#!/usr/bin/env bash
# Entrypoint guard tests (#1349 T11): the hosted entrypoint must
#   (1) REJECT the benchmark-only TORTOISE_EMBEDDER_OVERRIDE probe seam
#       (exit 1 with a clear message — the hosted image must never honor it),
#   (2) FATAL-exit 1 when the baked bge-small model cache is missing
#       (presence-only check; the bake is the ONLY source), and
#   (3) still exit 0 when the cache is present and no override is set
#       (the guards must not over-reject).
# The shell-level test is the ONLY exercise of the env-reject path — no
# Python test can reach entrypoint.sh.
#
# Usage: bash .ci-checks/check-entrypoint-guards.sh
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

OUT=$(mktemp)
trap 'rm -f "$OUT"' EXIT

PASS=0
FAIL=0

# run_case <desc> <expected_exit> <expected_grep> <args...>
run_case() {
  local desc="$1" want_exit="$2" want_grep="$3"
  shift 3
  set +e
  bash entrypoint.sh "$@" >"$OUT" 2>&1
  local got_exit=$?
  set -e
  if [ "$got_exit" -ne "$want_exit" ]; then
    echo "  FAIL: $desc — want exit $want_exit, got $got_exit" >&2
    cat "$OUT" >&2
    FAIL=$((FAIL + 1))
    return
  fi
  if ! grep -q "$want_grep" "$OUT"; then
    echo "  FAIL: $desc — expected output matching /$want_grep/ not found" >&2
    cat "$OUT" >&2
    FAIL=$((FAIL + 1))
    return
  fi
  echo "  ok: $desc (exit $got_exit)"
  PASS=$((PASS + 1))
}

echo "== check: TORTOISE_EMBEDDER_OVERRIDE rejected (env-reject, exit 1) =="
TORTOISE_EMBEDDER_OVERRIDE=benchmark-only run_case \
  "env-reject exits 1 with FATAL message" 1 "TORTOISE_EMBEDDER_OVERRIDE"
# Defensive unset: bash restores a function-call temp assignment after the
# call returns (so this is a no-op there), but POSIX sh persists it — the
# remaining cases must genuinely exercise the cache guards, not the env-reject.
unset TORTOISE_EMBEDDER_OVERRIDE

echo ""
echo "== check: missing bge-small cache FATAL-exits 1 =="
TMPHF=$(mktemp -d)
trap 'rm -rf "$TMPHF"; rm -f "$OUT"' EXIT
HF_HOME="$TMPHF" SENTENCE_TRANSFORMERS_HOME="$TMPHF" run_case \
  "missing-cache FATAL exits 1" 1 "models--BAAI--bge-small-en-v1.5"

echo ""
echo "== check: present cache + no override exits 0 (guards don't over-reject) =="
mkdir -p "$TMPHF/models--BAAI--bge-small-en-v1.5"
HF_HOME="$TMPHF" SENTENCE_TRANSFORMERS_HOME="$TMPHF" run_case \
  "present cache exits 0" 0 "skipping embedding pre-warm"

echo ""
if [ "$FAIL" -gt 0 ]; then
  echo "⛔ $FAIL entrypoint guard check(s) failed"
  exit 1
fi
echo "✅ entrypoint guards: $PASS checks passed"
