#!/usr/bin/env bash
# docs-link-check.sh — the `docs` job's LINK PRODUCER, and the verifier of its
# own artifact.
#
# WHY THIS IS A SCRIPT WE OWN (#9708; measured 2026-10-25 00:14Z, job 121024630636
# on pi-runner-cell3-x64-2)
#
# The step used to be `lycheeverse/lychee-action@v2`. On that runner the composite
# action reported **success** while its binary was missing, and it left the
# consumer a **0-byte** report:
#
#   Install lychee                     -> success
#   entrypoint.sh: line 49: lychee: command not found
#   entrypoint.sh: line 58: lychee: command not found
#   ##[end-action ... outcome=success;conclusion=success]
#   ##[error]docs-lint baseline: lychee JSON report ....json is unparseable:
#            Expecting value: line 1 column 1 (char 0)
#
# Two independent defects, both reproduced offline (see
# `tests/test_docs_link_check.py`):
#
#  1. **The binary was not resolvable from the step's PATH.** The action installs
#     into `$RUNNER_TEMP/lychee/bin` and puts that directory on `$GITHUB_PATH`
#     from INSIDE the composite, and a `$GITHUB_PATH` entry written by a composite
#     sub-step is not visible to the action's own later sub-steps on every runner
#     (it worked on pi-runner-vm-2 in the same hour, and on pi-runner-cell3-x64-1
#     once the same binary was on the launcher's PATH). Making the measurement
#     depend on an AMBIENT PATH is the defect: this script resolves the linter by
#     absolute path and, if it is absent, INSTALLS the pinned release itself.
#
#  2. **Exit 127 was swallowed into an empty file.** `entrypoint.sh` does
#     `LYCHEE_TMP="$(mktemp)"`, then `eval lychee ...`; the `[ ! -f "$LYCHEE_TMP" ]`
#     guard meant to catch "lychee never ran" can never fire (mktemp just created
#     the file), so it `cat`s the empty temp file over the consumer's report and
#     exits 0 with `fail: false`. The result is a file that EXISTS and is EMPTY —
#     which the differ can only report as "unparseable", i.e. as a problem with
#     the PR's diff. Reproduced verbatim, and also with a `lychee` stub that runs
#     and writes nothing: the silent pass does not depend on the binary being
#     missing.
#
# So the contract here is: **a step that produces no report fails, and names
# itself.** It never hands the differ a zero-byte file, and it never presents an
# absent measurement as a lint verdict.
#
# WHAT IS *NOT* CHANGED — #7435 (owner ruling, Option 1)
#
# Broken links found by lychee are still NOT this step's verdict: the step passes
# on lychee's 0 (`no findings`) and 2 (`link check failures`), and
# `tools/docs_lint_baseline.py check` decides whether those findings are already
# in the committed snapshot. The snapshot is a CEILING, not an amnesty.
#
# ⚠️ THE EXIT CODES ARE 0/2, NOT 0/1. lychee's own table (lychee 0.24.2
# `README.md` §Exit codes, shipped in the release asset; measured on the pinned
# binary 2026-10-25):
#
#   0  Success
#   1  Missing inputs or any unexpected runtime failures or configuration errors
#   2  Link check failures — at least one non-excluded link failed the check
#   3  Errors in the config file
#
# So the first revision of this script, which accepted {0,1}, was INVERTED: it
# failed the required `docs` check on every broken link (the #7435 churn it
# exists to prevent — `config/docs-lint-baseline.json` carries 46 known lychee
# findings) while accepting a runtime failure that wrote a partial report as a
# verdict. `tools/docs_lint_baseline.py::_run_lychee` never had to discover this
# because it ignores the exit code entirely; the polarity is pinned HERE.
#
# USAGE
#
#   docs-link-check.sh --files-from <newline list> --output <report.json>
#                      [--label "<step identity for ::error:: lines>"]
#
# EXIT
#
#   0  a report was produced (whatever lychee found)
#   1  the producer failed — the message names this step, the exit code and stderr
#   2  usage
#
# ENV (test seams, deliberately few)
#
#   DOCS_LINK_CHECK_LYCHEE          use exactly this binary (no download fallback)
#   DOCS_LINK_CHECK_ALLOW_DOWNLOAD  "0" = never download; absence must fail loudly

set -uo pipefail

# Pinned to match `LYCHEE_PIN` in tools/docs_lint_baseline.py and the `update`
# path that generated `config/docs-lint-baseline.json`: lychee's status text is
# version dependent and the key can carry it (the `error:` placeholder), so the
# producer must be the version the snapshot was taken with.
LYCHEE_VERSION="0.24.2"

LABEL="docs link check"
FILES_FROM=""
OUTPUT=""

usage() {
  echo "usage: $0 --files-from <list> --output <report.json> [--label <name>]" >&2
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --files-from) [ "$#" -ge 2 ] || usage; FILES_FROM="$2"; shift 2 ;;
    --output)     [ "$#" -ge 2 ] || usage; OUTPUT="$2"; shift 2 ;;
    --label)      [ "$#" -ge 2 ] || usage; LABEL="$2"; shift 2 ;;
    -h|--help)    usage ;;
    *)            usage ;;
  esac
done
[ -n "$FILES_FROM" ] || usage
[ -n "$OUTPUT" ] || usage

# Every failure goes through here, so no path can fail without saying who it is.
fail() {
  echo "::error::${LABEL}: $*" >&2
  exit 1
}

TMP_ROOT="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
BIN_DIR="$TMP_ROOT/lychee/bin"

# lychee prints `lychee 0.24.2`; take the first semver it names, so a build
# suffix or a leading warning line cannot be mistaken for the pin.
reports_pinned_version() {
  local binary="$1" out version
  [ -x "$binary" ] || return 1
  out="$("$binary" --version 2>&1)" || return 1
  version="$(printf '%s' "$out" \
    | sed -n 's/.*[^0-9]\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' \
    | head -n 1)"
  [ "$version" = "$LYCHEE_VERSION" ]
}

# sha256 of the release ASSET, per host arch — verified BEFORE extraction, so a
# changed or truncated upload cannot become the linter that writes the report.
pinned_sha256() {
  case "$1" in
    x86_64)  echo "1f4e0ef7f6554a6ed33dd7ac144fb2e1bbed98598e7af973042fc5cd43951c9a" ;;
    aarch64) echo "91a7bd65685da41b90ccb9bc867a3d649a7818042dae04ff405e55a25bddee4c" ;;
    *)       return 1 ;;
  esac
}

install_pinned_lychee() {
  local arch sha url work rc got binary dest
  arch="$(uname -m)"
  sha="$(pinned_sha256 "$arch")" || fail \
    "no pinned lychee ${LYCHEE_VERSION} release for host arch '${arch}' — refusing to run an unpinned linter"
  url="https://github.com/lycheeverse/lychee/releases/download/lychee-v${LYCHEE_VERSION}/lychee-${arch}-unknown-linux-gnu.tar.gz"
  work="$TMP_ROOT/lychee-install"
  rm -rf "$work" && mkdir -p "$work"

  curl -fsSL --retry 2 --retry-delay 2 --max-time 180 -o "$work/lychee.tar.gz" "$url"
  rc=$?
  [ "$rc" -eq 0 ] || fail \
    "could not download lychee ${LYCHEE_VERSION} from ${url} (curl exit ${rc}) — the link check cannot run, and this is not a finding about the diff"

  got="$(command -v sha256sum >/dev/null 2>&1 \
    && sha256sum "$work/lychee.tar.gz" | awk '{print $1}' \
    || shasum -a 256 "$work/lychee.tar.gz" | awk '{print $1}')"
  [ "$got" = "$sha" ] || fail \
    "the lychee ${LYCHEE_VERSION} asset for ${arch} has sha256 ${got}, expected ${sha} — refusing to run it"

  tar -xzf "$work/lychee.tar.gz" -C "$work" || fail \
    "could not extract the lychee ${LYCHEE_VERSION} asset for ${arch}"
  # The archive nests the binary one directory down. Bounded walk, explicit root.
  binary="$(find "$work" -maxdepth 2 -type f -name lychee | head -n 1)"
  [ -n "$binary" ] || fail \
    "the lychee ${LYCHEE_VERSION} asset for ${arch} carries no lychee binary — did the archive layout change?"

  mkdir -p "$BIN_DIR" || fail "could not create ${BIN_DIR}"
  dest="$BIN_DIR/lychee"
  install -m 0755 "$binary" "$dest" || fail "could not install lychee into ${BIN_DIR}"
  reports_pinned_version "$dest" || fail \
    "the lychee installed at ${dest} does not report version ${LYCHEE_VERSION}"
  echo "${LABEL}: provisioned lychee ${LYCHEE_VERSION} at ${dest} (sha256 ${sha})"
}

# ── resolve the linter ───────────────────────────────────────────────────────
# A stale report from an earlier step/job must never survive a producer failure:
# if this step fails and a consumer is (ever) allowed to run anyway, it must see
# NO report rather than the previous one. Cleared BEFORE any early-exit path.
STDERR_LOG="${OUTPUT}.stderr"
TMP_REPORT="${OUTPUT}.tmp"
rm -f "$STDERR_LOG" "$TMP_REPORT" "$OUTPUT"

# Never from PATH alone. Absolute candidates first, then a pinned install.
LYCHEE_BIN=""
if [ -n "${DOCS_LINK_CHECK_LYCHEE:-}" ]; then
  # An explicit override is a contract, not a hint: it must be usable, or we
  # fail rather than silently measuring with something else.
  reports_pinned_version "$DOCS_LINK_CHECK_LYCHEE" || fail \
    "DOCS_LINK_CHECK_LYCHEE=${DOCS_LINK_CHECK_LYCHEE} is not an executable reporting lychee ${LYCHEE_VERSION}"
  LYCHEE_BIN="$DOCS_LINK_CHECK_LYCHEE"
elif reports_pinned_version "$BIN_DIR/lychee"; then
  LYCHEE_BIN="$BIN_DIR/lychee"
elif command -v lychee >/dev/null 2>&1 && reports_pinned_version "$(command -v lychee)"; then
  LYCHEE_BIN="$(command -v lychee)"
elif [ "${DOCS_LINK_CHECK_ALLOW_DOWNLOAD:-1}" = "0" ]; then
  fail "lychee ${LYCHEE_VERSION} is not available on this runner (not at ${BIN_DIR}/lychee, not on PATH) and downloading is disabled — the link check did not run"
else
  install_pinned_lychee
  LYCHEE_BIN="$BIN_DIR/lychee"
fi

# ── the population ──────────────────────────────────────────────────────────
# A list with no entries would lint nothing and still report a verdict.
# markdownlint's half refuses this through its `Linting: N` == changed-set check;
# the link half has no equivalent positive evidence in lychee's JSON, so the
# population is checked here — a BLANK line does not make a population, which is
# what the caller's `grep -c .` counts.
if [ ! -s "$FILES_FROM" ] || ! grep -q '[^[:space:]]' "$FILES_FROM"; then
  fail "the changed-file list ${FILES_FROM} is missing, empty or blank — refusing to report a link verdict over nothing"
fi

# ── run, capture, and verify ────────────────────────────────────────────────
STDERR_LOG="${OUTPUT}.stderr"
TMP_REPORT="${OUTPUT}.tmp"

# `--mode task` / `--format json` / `--files-from`: the same invocation
# `tools/docs_lint_baseline.py::_run_lychee` uses to GENERATE the snapshot, so the
# producer and the generator cannot drift apart in what they ASK for. They do
# differ on what they DO with the answer: `_run_lychee` ignores
# `proc.returncode`, which is exactly why the verdict polarity could be wrong
# here without any test noticing — it is pinned in this script (see the exit-code
# table in the header), not in the generator.
"$LYCHEE_BIN" --mode task --no-progress --format json \
  --files-from "$FILES_FROM" >"$TMP_REPORT" 2>"$STDERR_LOG"
rc=$?

stderr_tail() {
  [ -s "$STDERR_LOG" ] || { echo "(empty)"; return; }
  head -c 600 "$STDERR_LOG" | tr '\n' ' '
}

# (1) Did it write anything? This is the check the composite action lacked: a
#     file that exists and is EMPTY is "no report", never "no findings".
if [ ! -s "$TMP_REPORT" ]; then
  rm -f "$TMP_REPORT"
  fail "lychee produced no report: exit ${rc}, stderr: $(stderr_tail)"
fi

# (2) Is what it wrote a lychee document this differ can read? Same three keys
#     `_require_lychee_shape` requires — checked HERE so a malformed report is
#     the producer's failure, not the diff's.
verified="$(python3 - "$TMP_REPORT" 2>&1 <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        document = json.load(handle)
except (OSError, json.JSONDecodeError) as exc:
    sys.exit(f"unreadable JSON: {exc}")

missing = [key for key in ("total", "error_map", "timeout_map") if key not in document]
if missing:
    sys.exit(f"missing {'/'.join(missing)}")
print(
    f"total={document['total']} errors={len(document['error_map'])} "
    f"timeouts={len(document['timeout_map'])}"
)
PY
)" || fail "lychee wrote a report this differ cannot read: ${verified} (exit ${rc}, stderr: $(stderr_tail))"

# (3) Did lychee COMPLETE? 0 = success, 2 = link check failures — both are a
#     verdict and the differ owns it (#7435). 1 (missing inputs / runtime /
#     config error) and 3 (config file error) are a FAILED measurement, and a
#     report written on the way out is not evidence about the diff.
case "$rc" in
  0|2) : ;;
  *)
    rm -f "$TMP_REPORT"
    fail "lychee exited ${rc} — that is not a link verdict (0/2 are; 1 and 3 are runtime/config failures), so no report was published; stderr: $(stderr_tail)"
    ;;
esac

mv "$TMP_REPORT" "$OUTPUT" || fail "could not publish the report to ${OUTPUT}"

echo "${LABEL}: lychee ${LYCHEE_VERSION} exit=${rc} ${verified} report=${OUTPUT} bytes=$(wc -c <"$OUTPUT" | tr -d ' ')"
if [ -s "$STDERR_LOG" ]; then
  echo "${LABEL}: lychee stderr:"
  cat "$STDERR_LOG"
fi
# The differ reports a KNOWN finding as a count only, so the raw report is also
# published into the log — the visibility half of "the snapshot is a ceiling".
cat "$OUTPUT"
exit 0
