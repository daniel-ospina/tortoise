#!/usr/bin/env bash
# docs-markdownlint.sh — the `docs` job's MARKDOWNLINT producer, and the verifier
# of its own artifact.
#
# WHY THIS IS A SCRIPT WE OWN (#10265; measured 2026-10-29T01:18Z, job 123190229185
# on `gh-runner-x64-4`, and 2026-10-28T23:55Z, job 123140492354 on
# `pi-runner-x64-4`)
#
# The step used to be
#
#   xargs -0 -a "$RUNNER_TEMP/pr-md.nul" npx --yes markdownlint-cli2@0.23.3
#
# and on those runs the report it captured was
#
#   sh: 1: markdownlint-cli2: not found
#   markdownlint exit=123 (informational — xargs rewrites it; the differ reads
#                          the report)
#   ##[error]docs-lint baseline: markdownlint report .../mdlint-pr.txt has no
#            'Linting:' line — it did not run
#
# `npx` itself ran, so node existed; what failed was npx's EXEC of the package. It
# is not a registry problem: `npx --yes <pkg>@<ver>` reuses a shared `_npx` cache
# keyed by the spec, and when that cached tree's `node_modules/.bin` link is
# partial npx still believes it is installed and execs the bin through `sh`,
# which cannot resolve it. So the REQUIRED `docs` check depended on the state of
# a SHARED CACHE on whichever runner it was scheduled onto — measured on two
# different pools in one day, and re-running was the only lever a lane had.
#
# The differ fails CLOSED on that (correctly — no measurement is not a verdict),
# but its annotation reads as a finding about the PR's markdown, which sent at
# least one lane to the changed-file set first. Both halves are fixed here, the
# same way `docs-link-check.sh` fixes them for lychee (#9708):
#
#   1. **No shared cache, no ambient PATH.** The pinned version is installed into
#      a PRIVATE prefix and invoked by ABSOLUTE path, so nothing another job did
#      to a shared cache can decide whether this required check can measure.
#   2. **The report proves WHICH linter produced it.** The run is verified
#      against the banner (`markdownlint-cli2 v<pin>`) and the positive-evidence
#      `Linting: N` line before it is published, so a producer that did not run
#      fails HERE, naming the linter and saying plainly that it is not a finding
#      about the diff.
#
# WHAT IS *NOT* CHANGED — #7435 (owner ruling, Option 1)
#
# Findings are CAPTURED here, never adjudicated: this script exits 0 whenever the
# linter RAN, whatever it found, and `tools/docs_lint_baseline.py check` decides
# from the committed snapshot whether any finding is new. The snapshot is a
# CEILING, not an amnesty.
#
# The population is NOT re-counted here either: `--markdownlint-expected-files`
# is the caller's changed-set count and the differ owns that comparison. This
# script only refuses to publish a report that no pinned linter produced.
#
# USAGE
#
#   docs-markdownlint.sh --cli2 <pkg>@<version> --files-from <nul list>
#                        --output <report.txt> [--label <step identity>]
#
# EXIT
#
#   0  a report was produced by the pinned linter (whatever it found)
#   1  the producer failed — the message names THIS step and the linter
#   2  usage
#
# ENV (test seams, deliberately few)
#
#   DOCS_MARKDOWNLINT_BIN            use exactly this binary (no install fallback)
#   DOCS_MARKDOWNLINT_ALLOW_INSTALL  "0" = never install; absence must fail loudly

set -uo pipefail

LABEL="docs markdownlint"
CLI2_SPEC=""
FILES_FROM=""
OUTPUT=""

usage() {
  echo "usage: $0 --cli2 <pkg>@<version> --files-from <list> --output <report>" \
       "[--label <name>]" >&2
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --cli2)       [ "$#" -ge 2 ] || usage; CLI2_SPEC="$2"; shift 2 ;;
    --files-from) [ "$#" -ge 2 ] || usage; FILES_FROM="$2"; shift 2 ;;
    --output)     [ "$#" -ge 2 ] || usage; OUTPUT="$2"; shift 2 ;;
    --label)      [ "$#" -ge 2 ] || usage; LABEL="$2"; shift 2 ;;
    -h|--help)    usage ;;
    *)            usage ;;
  esac
done
[ -n "$CLI2_SPEC" ] || usage
[ -n "$FILES_FROM" ] || usage
[ -n "$OUTPUT" ] || usage

# The pin is the package spec's version: the workflow states it once and this
# script verifies the run against it, so a floating linter cannot drift the rule
# surface under a required check (#2386).
case "$CLI2_SPEC" in
  *@[0-9]*) CLI2_PIN="${CLI2_SPEC##*@}" ;;
  *) usage ;;
esac

# Every failure goes through here, so no path can fail without saying who it is.
fail() {
  echo "::error::${LABEL}: $*" >&2
  exit 1
}

TMP_ROOT="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
PREFIX="$TMP_ROOT/mdlint-cli2"
BIN="$PREFIX/node_modules/.bin/markdownlint-cli2"

install_pinned_cli2() {
  local log="$TMP_ROOT/mdlint-cli2-install.log" rc
  command -v npm >/dev/null 2>&1 || fail \
    "npm is ABSENT on this runner, so ${CLI2_SPEC} cannot be installed" \
    " — the linter did not run, and this is NOT a finding about the changed markdown"
  rm -rf "$PREFIX" && mkdir -p "$PREFIX"
  npm install --prefix "$PREFIX" --no-audit --no-fund --loglevel=error \
    "$CLI2_SPEC" >"$log" 2>&1
  rc=$?
  [ "$rc" -eq 0 ] || fail \
    "could not install ${CLI2_SPEC} into ${PREFIX} (npm exit ${rc}; $(install_tail "$log"))" \
    " — the linter is ABSENT on this runner, so no lint verdict exists and this is NOT a finding about the changed markdown"
}

install_tail() {
  [ -s "$1" ] || { printf '(no installer output)'; return; }
  tail -c 400 "$1" | tr '\n' ' '
}

# ── resolve the linter ───────────────────────────────────────────────────────
# A report from an earlier step/job must never survive a producer failure: if
# this step fails and a consumer is (ever) allowed to run anyway, it must see NO
# report rather than the previous one. Cleared BEFORE any early-exit path.
TMP_REPORT="${OUTPUT}.tmp"
rm -f "$TMP_REPORT" "$OUTPUT"

# Never from a shared cache, and never from an AMBIENT PATH: the private prefix
# or nothing. A `markdownlint-cli2` that happens to be on PATH is a version this
# check never asked for — resolving it would let another job's toolchain decide
# what the required check measures (the #9708 lesson, one step over).
if [ -n "${DOCS_MARKDOWNLINT_BIN:-}" ]; then
  # An explicit override is a contract, not a hint: it must be executable, or we
  # fail rather than silently measuring with something else.
  [ -x "$DOCS_MARKDOWNLINT_BIN" ] || fail \
    "DOCS_MARKDOWNLINT_BIN=${DOCS_MARKDOWNLINT_BIN} is not an executable — the linter did not run"
  BIN="$DOCS_MARKDOWNLINT_BIN"
elif [ -x "$BIN" ]; then
  :
elif [ "${DOCS_MARKDOWNLINT_ALLOW_INSTALL:-1}" = "0" ]; then
  fail "${CLI2_SPEC} is not available on this runner (nothing at ${BIN}) and installing is disabled — the lint did not run"
else
  install_pinned_cli2
  [ -x "$BIN" ] || fail \
    "installing ${CLI2_SPEC} produced no executable at ${BIN} — the lint did not run"
fi

# ── the population ──────────────────────────────────────────────────────────
# `markdownlint-cli2` with NO file arguments lints its config's globs (the whole
# repo), so an empty list must never reach it: that would turn a docs-only check
# into a full-repo lint. A list of BLANK lines is not a population either.
if [ ! -f "$FILES_FROM" ] || ! grep -q '[^[:space:]]' "$FILES_FROM"; then
  fail "the changed-file list ${FILES_FROM} is missing, empty or blank — refusing to run the linter over nothing"
fi

# ── run, capture, and verify ────────────────────────────────────────────────
# NUL-delimited ARGV via xargs — a filename is data, never shell text (#4449).
# The list arrives on STDIN, not through `-a FILE`: `-a` is a GNU extension and
# BSD/macOS xargs rejects it — measured, `xargs: invalid option -- a`, which reds
# six of this producer's own tests on every macOS dev box while CI's
# ubuntu-latest runners stay green. `-a FILE` is defined as "read the arguments
# from FILE", so the stdin redirect is the same semantics and both agree.
# The exit code is deliberately NOT the verdict: GNU xargs rewrites a child's
# exit 1-125 to 123, so cli2's 1 (findings) and 2 (fatal) are indistinguishable
# here. The REPORT is the evidence — the banner names the linter that ran, and
# `Linting: N` is the positive evidence that it judged what it was handed.
#
# ⚠️ STDERR IS PART OF THE REPORT. Measured on the pinned linter: cli2 prints its
# banner, `Linting:` and `Summary:` on STDOUT and its FINDINGS on STDERR, and
# `tools/docs_lint_baseline.py` parses one merged document (it cross-checks the
# `Summary:` count against the findings it parsed). Splitting the streams here —
# the obvious-looking tidy-up — leaves the differ reporting "1 issue(s) but 0
# parsed" and failing closed on a real finding, so `2>&1` is load-bearing.
xargs -0 "$BIN" >"$TMP_REPORT" 2>&1 <"$FILES_FROM"
rc=$?

report_tail() {
  [ -s "$TMP_REPORT" ] || { echo "(empty)"; return; }
  head -c 600 "$TMP_REPORT" | tr '\n' ' '
}

# (1) Did it write anything? A file that exists and is EMPTY is "no report",
#     never "no findings".
if [ ! -s "$TMP_REPORT" ]; then
  rm -f "$TMP_REPORT"
  fail "the linter produced no report: exit ${rc}" \
    " — the lint did not run, and this is NOT a finding about the changed markdown"
fi

# (2) Did the PINNED linter produce it? cli2 prints its own identity on every
#     run; `--version` is not a version flag for cli2 (it is parsed as a glob
#     pattern — measured), so the banner is the only honest source.
banner="$(head -n 1 "$TMP_REPORT" | tr -d '\r')"
case "$banner" in
  "markdownlint-cli2 v${CLI2_PIN} "*) : ;;
  *)
    rm -f "$TMP_REPORT"
    fail "the report does not come from ${CLI2_SPEC}: first line is '${banner}'" \
      " — the pinned linter did not run, so this is NOT a finding about the changed markdown"
    ;;
esac

# (3) Is `Linting: N` present? It is the positive evidence that cli2 actually
#     judged the files it was handed — the banner prints before a config error
#     aborts the run, and the exit code cannot supply it (see above).
if ! grep -qE '^Linting: [0-9]+ files?$' "$TMP_REPORT"; then
  rm -f "$TMP_REPORT"
  fail "the report has no 'Linting:' line (exit ${rc}; report tail: $(report_tail))" \
    " — the lint did not run, and this is NOT a finding about the changed markdown"
fi

mv "$TMP_REPORT" "$OUTPUT" || fail "could not publish the report to ${OUTPUT}"

echo "${LABEL}: ${CLI2_SPEC} exit=${rc} (informational — xargs rewrites it; the differ reads the report) $(grep -E '^Linting: [0-9]+ files?$' "$OUTPUT" | head -n 1) report=${OUTPUT} bytes=$(wc -c <"$OUTPUT" | tr -d ' ')"
# The differ reports a KNOWN finding as a count only, so the raw report is also
# published into the log — the visibility half of "the snapshot is a ceiling".
cat "$OUTPUT"
exit 0
