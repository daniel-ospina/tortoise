#!/usr/bin/env bash
# run-with-eval-keys.sh — run an eval/measurement command against the REPO
# `.env` provider keys, deterministically and self-declared (#2718 / #4860).
#
# WHY THIS EXISTS
# ---------------
# Eval/measurement entry points read provider keys straight from the process
# env, and they do NOT load the repo `.env`: `tortoise.mcp_server::_load_dotenv`
# is the repo's only `.env` loader, and the eval runner does not import it. The
# one loader that does exist only fills keys that are ABSENT — "never override
# an explicitly set (even empty) environment variable" — so even where it runs,
# an ambient key wins. Either way the key a run bills is whatever the calling
# shell exported (this fleet sources `~/pi-keys.env`), NOT the repo `.env`. On
# 2026-09-23 the sealed #2552 write-path measurement silently billed the ambient
# fleet OpenRouter key (which was exhausted: `limit=100, limit_remaining=0`) and
# returned HTTP 403 on all 7 sessions, while a healthy evals key sat in `.env`
# the whole time — and nothing in the run output said which key had been used.
#
# WHAT IT DOES
# ------------
#   1. STRIPS the ambient provider keys it owns (below).
#   2. Loads the repo-root `.env` with explicit OVERRIDE for those keys, so the
#      evals key deterministically wins. Every *other* `.env` key keeps
#      `_load_dotenv`'s deliberate never-override semantics, so an explicitly
#      exported `TORTOISE_DB_URI` still wins (that behaviour is intentional).
#   3. Prints a source + fingerprint line for each managed key, so the key a run
#      used is knowable from its output. The fingerprint shows the first 6
#      characters (the provider's fixed prefix, plus for some issuers a few key
#      characters), the length, and a sha256 prefix; the full value is never
#      printed. Paste the lines into the run receipt.
#   4. `exec`s the command, so the wrapper process becomes the command (signals
#      land on the real process and the exported env is never lost).
#
# USAGE
# -----
#   tools/run-with-eval-keys.sh <command> [args...]
#
# Sealed write-path measurement (product/llm lane):
#   PYTHONPATH=$PWD TORTOISE_TEST_CARVE_OUT=1 \
#     tools/run-with-eval-keys.sh \
#       .venv/bin/python -m tests.eval.write_path.runner run --out <receipt>
#
# The declaration lines go to STDERR (they must never corrupt the wrapped
# command's data channel). Capture them with `2>&1` or `2>receipt.keys` when a
# receipt must carry them.
#
# ENV
# ---
#   EVAL_KEYS_ENV_FILE   override the .env path (tests / alternate checkouts);
#                        default is <repo-root>/.env.
#
# This wrapper only READS `.env`. It never writes it.
#
# The `_RWEK_`/`_rwek_` namespace belongs to this script: the loader SKIPS any
# `.env` entry named into it, so a `.env` can never rewrite the launcher's own
# state (the env file path, the source label, the managed-key set). There is no
# `eval` on a `.env`-derived name anywhere below.

# A caller can export a shell FUNCTION through the environment
# (`BASH_FUNC_<name>%%`) that shadows a builtin — `unset`, `export`, `set`,
# `printf`, `[`, and `builtin`/`declare`/`command` — so there is no in-process
# repair: the repair would run through the shadow. `bash -p` (privileged mode)
# imports NO shell functions and ignores `SHELLOPTS`/`BASH_ENV`, so the launcher
# re-execs itself under it before any shadowable builtin that matters runs. The
# guard below is `case` (a reserved word — not shadowable), parameter expansion,
# and the `export`/`exec` builtins.
#
# `_RWEK_SANITIZED` is that guard's marker and is dropped again immediately, so
# it cannot reach the wrapped command or a NESTED invocation of this launcher —
# a nested run that skipped sanitization would print a receipt with no truth in
# it. It is still a caller-settable opt-out, and that opt-out is a HOLE in this
# guard, not a cost-free switch. Every hole needs the caller to export something
# (`_RWEK_SANITIZED=1`, or a function), and each is stated here as measured:
#
#   - `_RWEK_SANITIZED=1` skips the re-exec, so the caller's own function
#     shadows stay live for the whole run. The two fail-closed checks below
#     (tracing, ambient strip) still EVALUATE — their condition is `case` and
#     parameter expansion, which no function can shadow — and neither RESPONSE
#     goes through a shadowable builtin: each execs `"$_RWEK_BASH" -p -c …`, an
#     ABSOLUTE path captured before anything else runs (so it is never a function
#     lookup and a `.env` line cannot repoint it) into a child that imports no
#     exported functions. That prints and exits 3 whatever `printf`/`exit`/`set`
#     the caller shadowed. `exec` itself is the one builtin left that matters: a
#     caller who shadows it makes those responses no-ops. The ambient-strip path
#     then continues, and its receipt stays TRUE on every line because the state
#     it reads (`_RWEK_ABORTED`, the per-key `_RWEK_FROM_FILE` string, the header
#     text) is assigned — assignments are not builtins — and because a key is
#     recorded as coming from the file only after the export is PROVEN to have
#     taken (`declare -p` shows `-x` AND the value matches the file's). A value
#     that merely coincides with the file's is indistinguishable from a survivor
#     and is labelled with the file whose value it matches. The TRACING path has
#     no such fallback: with `exec` shadowed, xtrace stays ON and bash prints the
#     `.env` lines it reads as well as the loader's `export`, so a key can appear
#     in stderr. Shadowing `exec` is therefore the one thing that breaks the "no
#     value is printed" guarantee as well as the silence one.
#   - a shadowed `exec` makes the re-exec AND the final `exec "$@"` no-ops, so the
#     wrapped command never runs. The sentinel after the final exec is an
#     absolute-path `"$_RWEK_BASH" -p -c …`, so it still reports that and exits 3
#     even with `exit`/`printf` shadowed — a shadowed `exec` can no longer look
#     like a silent success, and a `.env` line cannot silence it.
#
# What is left, and cannot be closed from inside: a caller who shadows `exec`
# gets exit 3 and no run, with a receipt whose per-key lines are still true, but
# with tracing ON it can leak a key through the traced `export`, and a caller who
# shadows EVERY builtin named above could suppress every message. This is an
# auditability guard, not a privilege boundary: the invoking principal supplies
# the ambient keys and can already read them without this script, so the
# guarantee is "no correct invocation is misreported or leaks", never "a hostile
# environment cannot stay silent". (A shadowed `export` only costs one extra
# re-exec hop — the unexported marker makes the `-p` child re-exec again — and the
# run is still sanitized.)
#
# The interpreter's own ABSOLUTE path is read once here — before the guard, and
# long before `.env` is opened. The fail-closed responses exec this path precisely
# because a slash-qualified word is not a function lookup; letting a `.env` line
# name `BASH` would repoint them (bash does not export `BASH`, so the loader's
# fill-if-absent branch would export a `.env` value over it) and swallow the
# sentinel.
_RWEK_BASH=${BASH:-/bin/bash}

case "${_RWEK_SANITIZED:-}" in
  '')
    _RWEK_SANITIZED=1
    export _RWEK_SANITIZED
    exec "$_RWEK_BASH" -p "$0" "$@"
    ;;
esac
unset _RWEK_SANITIZED

set -u

# An inherited `SHELLOPTS=xtrace` traces `export "$key=$value"` and writes the
# FULL key to stderr — the channel this tool exists to keep clean for receipts.
# (`-p` already ignores `SHELLOPTS`; this covers an explicit `bash -x`.) Tracing
# is turned off, and if it is somehow still on the launcher refuses to run
# rather than print a key.
case $- in *x*) set +x ;; esac
case $- in
  *x*)
    # Same absolute-path form as the ambient-strip refusal below, for the same
    # reason: `printf`/`exit` are builtins a caller can shadow, and a shadowed
    # `exit` here would leave xtrace ON — printing the full key on the loader's
    # `export` line after this message had claimed the run was refused.
    exec "$_RWEK_BASH" -p -c 'printf "[eval-keys] FATAL: shell tracing is on and would print a key; refusing to run\n" >&2; exit 3'
    ;;
esac

# The provider keys this wrapper owns — the set the extraction/reader paths
# actually read:
#   tortoise/ingest.py::_PROVIDERS          → OPENROUTER / DEEPSEEK / OPENAI / GEMINI
#   tortoise/analyze.py::_LLM_PROVIDERS     → DEEPSEEK / OPENAI
#   tortoise/model_adapters.py              → VENICE (ask + longmem lanes)
# ANTHROPIC_API_KEY is deliberately NOT managed: no tortoise provider reads it
# (hosted_api.py::_llm_provider_keys documents the same exclusion), so managing
# it would only mask unrelated host noise. Hand-maintained here, but PINNED
# against those registries by
# tests/test_run_with_eval_keys.py::test_managed_keys_match_the_code_registries —
# adding a provider key to a registry without managing it here reddens that test.
_RWEK_MANAGED_KEYS=(
  OPENROUTER_API_KEY
  DEEPSEEK_API_KEY
  VENICE_API_KEY
  OPENAI_API_KEY
  GEMINI_API_KEY
)
# Which managed keys THIS run took from the env file (space-delimited, so it is
# safe under `set -u` in bash 3.2, where an empty array's `"${a[@]}"` is an
# unbound-variable error). Only consulted when the strip proof failed, to keep
# each receipt line truthful about that one key.
_RWEK_FROM_FILE=

# Absolute form of a path whose target directory exists (else the value as-is).
# `pwd -P` so a symlinked directory cannot leak a false path into the label.
absolute_path() {
  local p=$1 d b
  d=$(dirname -- "$p")
  b=$(basename -- "$p")
  if [ -d "$d" ]; then
    (CDPATH= cd -- "$d" && printf '%s/%s' "$(pwd -P)" "$b")
  else
    printf '%s' "$p"
  fi
}

# The wrapper's own real location, symlinks resolved (macOS has no `readlink -f`),
# so `<repo-root>/.env` names the REAL repo root even when the wrapper is invoked
# through a symlink or a bare PATH lookup — otherwise `$0`'s directory would
# derive a foreign repo root and the `.env` label would name a file the wrapper
# never read.
resolve_self() {
  local p=$1 d n=0
  case "$p" in
    */*) ;;
    *) p=$(command -v "$p" 2>/dev/null) || p=$1 ;;
  esac
  while [ -L "$p" ] && [ "$n" -lt 40 ]; do
    n=$((n + 1))
    d=$(dirname -- "$p")
    p=$(readlink "$p")
    case "$p" in
      /*) ;;
      *) p=$d/$p ;;
    esac
  done
  absolute_path "$p"
}

# Resolve a symlink chain to its final target (display only — the alias path is
# what the wrapper opens). Bounded, so a symlink cycle cannot hang the launcher.
resolve_target() {
  local p=$1 d n=0
  while [ -L "$p" ] && [ "$n" -lt 40 ]; do
    n=$((n + 1))
    d=$(dirname -- "$p")
    p=$(readlink "$p")
    case "$p" in
      /*) ;;
      *) p=$d/$p ;;
    esac
  done
  printf '%s' "$p"
}

_RWEK_SCRIPT_PATH=$(resolve_self "$0")
_RWEK_SCRIPT_DIR=$(dirname -- "$_RWEK_SCRIPT_PATH")
_RWEK_REPO_ROOT=$(CDPATH= cd -- "$_RWEK_SCRIPT_DIR/.." && pwd -P)
_RWEK_ENV_FILE=${EVAL_KEYS_ENV_FILE:-$_RWEK_REPO_ROOT/.env}

# The declaration label. `.env` is used ONLY when the resolved env file IS the
# resolved repo-root `.env`; otherwise the absolute path is printed, so the
# label never names a file the wrapper did not read.
if [ -n "${EVAL_KEYS_ENV_FILE:-}" ]; then
  _RWEK_ENV_FILE=$(absolute_path "$_RWEK_ENV_FILE")
fi
if [ "$(absolute_path "$_RWEK_ENV_FILE")" = "$_RWEK_REPO_ROOT/.env" ]; then
  _RWEK_SOURCE_LABEL=.env
else
  _RWEK_SOURCE_LABEL=$_RWEK_ENV_FILE
fi

if [ "$#" -eq 0 ]; then
  printf 'usage: %s <command> [args...]\n' "$(basename -- "$0")" >&2
  exit 2
fi

is_managed_key() {
  local k=$1 m
  for m in "${_RWEK_MANAGED_KEYS[@]}"; do
    [ "$k" = "$m" ] && return 0
  done
  return 1
}

trim() {
  local s=$1
  s=${s#"${s%%[![:space:]]*}"}
  s=${s%"${s##*[![:space:]]}"}
  printf '%s' "$s"
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$1" | sha256sum | cut -c1-12
  elif command -v shasum >/dev/null 2>&1; then
    printf '%s' "$1" | shasum -a 256 | cut -c1-12
  elif command -v openssl >/dev/null 2>&1; then
    printf '%s' "$1" | openssl dgst -sha256 | awk '{print $NF}' | cut -c1-12
  else
    printf 'unavailable'
  fi
}

# ── 1. strip the ambient provider keys ────────────────────────────────────
# After this, a managed key can come from exactly one place: the repo `.env`.
# (Or be unset — which is the fail-closed outcome, never a silent fallback to
# the ambient key.)
unset "${_RWEK_MANAGED_KEYS[@]}"
# Prove the strip took. A shadowed `unset` cannot survive the `-p` re-exec
# above, but the check makes the receipt's honesty local: an ambient key left
# behind would otherwise be reported with `source=<the .env file>`, which is
# the exact false-provenance class (#4860) this launcher exists to remove.
for _rwek_key in "${_RWEK_MANAGED_KEYS[@]}"; do
  case "${!_rwek_key+x}" in
    ?*)
      # The response must not depend on a builtin a caller can shadow. `"$BASH"`
      # is an ABSOLUTE path (bash sets it at startup) and a slash-qualified word
      # is never a function lookup, so this runs with `printf`/`exit` shadowed;
      # `-p` makes the child drop exported functions, so its own printf is real.
      # `exec` replaces this process, so the wrapped command does NOT run.
      #
      # A caller who ALSO shadows `exec` turns that into a no-op, and the run
      # continues — which is why the state below is assigned FIRST: assignments
      # are not builtins, so they survive every shadow, and they are what keeps
      # the receipt from naming the env file as the source of a value the strip
      # failed to remove (#4860's false-attribution class).
      _RWEK_ABORTED=1
      exec "$_RWEK_BASH" -p -c 'printf "[eval-keys] FATAL: %s survived the ambient strip; refusing to run\n" "$1" >&2; exit 3' _rwek "$_rwek_key"
      ;;
  esac
done

# ── 2. load `.env` — explicit override for MANAGED keys, fill-if-absent for
#      everything else (never clobber an explicit TORTOISE_DB_URI). The
#      parsing semantics mirror `mcp_server._load_dotenv` on LF-separated
#      files: a leading `export ` is tolerated, blank/# lines are skipped,
#      quoted values are literal, and an unquoted value loses a ` #` inline
#      comment while a bare `#` survives. (One deliberate divergence: a bare
#      `\r` mid-line is NOT a line separator here, where Python's
#      `splitlines()` treats it as one — only a trailing `\r` (CRLF) is
#      stripped, which is what a `.env` written on this platform looks like.)
#
#      "Already set" is judged by exact variable NAME against the environment
#      this script INHERITED, plus the keys an earlier `.env` line already
#      filled — never by "is some shell variable set", which would also catch
#      bash's own non-exported internals (`PS4`, `IFS`, …) and silently drop a
#      `.env` key naming one. The question is asked in-process with the
#      `declare` BUILTIN (`builtin declare -p` reveals the `-x` flag), so no
#      external command is consulted and — the launcher having re-exec'd itself
#      under `bash -p` above — no exported function can shadow it.
if [ -f "$_RWEK_ENV_FILE" ] && [ -r "$_RWEK_ENV_FILE" ]; then
  while IFS= read -r _rwek_raw || [ -n "${_rwek_raw:-}" ]; do
    _rwek_line=$(trim "${_rwek_raw%$'\r'}")
    case "$_rwek_line" in
      '' | '#'*) continue ;;
    esac
    case "$_rwek_line" in
      'export '*) _rwek_line=${_rwek_line#export } ;;
    esac
    case "$_rwek_line" in
      *=*) ;;
      *) continue ;;
    esac

    _rwek_key=$(trim "${_rwek_line%%=*}")
    _rwek_value=$(trim "${_rwek_line#*=}")

    # Only well-formed shell/variable identifiers may be exported.
    case "$_rwek_key" in
      '' | [0-9]* | *[!A-Za-z0-9_]*) continue ;;
    esac
    # The launcher's own namespace is reserved, and enforced here rather than
    # merely documented: a `.env` entry named `_RWEK_*`/`_rwek_*` could
    # otherwise redefine the env-file path, the source label, or the managed
    # set from inside the file being read.
    case "$_rwek_key" in
      _RWEK_* | _rwek_*) continue ;;
    esac
    # `EVAL_KEYS_ENV_FILE` is this launcher's own INPUT (a plain name, so the
    # pattern above does not cover it). Left loadable, a `.env` line could set
    # it for the wrapped command, and a NESTED invocation would then read a
    # different env file — the "a `.env` can never rewrite the launcher's own
    # state" claim above would be false for every child.
    case "$_rwek_key" in
      EVAL_KEYS_ENV_FILE) continue ;;
    esac

    _rwek_first=${_rwek_value:0:1}
    _rwek_last=${_rwek_value: -1}
    if { [ "$_rwek_first" = '"' ] || [ "$_rwek_first" = "'" ]; } && [ "$_rwek_last" = "$_rwek_first" ]; then
      # mirrors _load_dotenv: bare quotes are literal, both ends stripped; a
      # lone quote char yields an empty value (Python's [1:-1] clamps)
      if [ "${#_rwek_value}" -ge 2 ]; then
        _rwek_value=${_rwek_value:1:${#_rwek_value}-2}
      else
        _rwek_value=""
      fi
    else
      case "$_rwek_value" in
        *' #'*) _rwek_value=$(trim "${_rwek_value%% #*}") ;;
      esac
    fi

    if is_managed_key "$_rwek_key"; then
      export "$_rwek_key=$_rwek_value"
      # Record the file as this key's source only if the file's value is the one
      # this process HOLDS **and** the one the wrapped command will actually
      # inherit (it must be exported). `export` and `builtin` are builtins a
      # caller can shadow — a shadow can assign without exporting, or fake a
      # `declare -x` probe — so the export flag is read from a FRESH privileged
      # shell: `"$_RWEK_BASH"` is an absolute path (never a function lookup) and
      # `-p` imports no exported functions, so that child's view IS the
      # environment the command gets, and nothing the caller exported can
      # shadow it. The value half is a direct expansion, also unshadowable.
      _rwek_proven=
      case "${!_rwek_key+x}" in
        ?*)
          case "${!_rwek_key}" in
            "$_rwek_value")
              _rwek_exported=$(
                "$_RWEK_BASH" -p -c '_rwek_n=$1; case "$(declare -p "$_rwek_n" 2>/dev/null)" in *-x*) printf 1 ;; esac' \
                  _rwek "$_rwek_key" 2>/dev/null
              )
              case "${_rwek_exported:-}" in
                ?*) _rwek_proven=1 ;;
              esac
              ;;
          esac
          ;;
      esac
      case "${_rwek_proven:-}" in
        ?*) _RWEK_FROM_FILE="$_RWEK_FROM_FILE $_rwek_key" ;;
        *) _RWEK_ABORTED=1 ;;
      esac
    else
      # fill-if-absent: an inherited key, or one an earlier `.env` line already
      # set, is never clobbered — `_load_dotenv`'s deliberate semantics. The
      # `declare` builtin reports the `-x` (exported) flag, so this is exactly
      # "is the name in the process environment", with no external command and
      # no exported function able to shadow the probe.
      _rwek_decl=$(builtin declare -p "$_rwek_key" 2>/dev/null) || _rwek_decl=
      _rwek_flags=${_rwek_decl#declare -}
      _rwek_flags=${_rwek_flags%% *}
      case "$_rwek_flags" in
        *x*) ;;                                 # exported — keep the value
        *) export "$_rwek_key=$_rwek_value" ;;
      esac
    fi
  done <"$_RWEK_ENV_FILE"
else
  printf '[eval-keys] WARNING: %s not found or not a regular file — no provider key loaded from .env (fail-closed)\n' \
    "$_RWEK_ENV_FILE" >&2
fi

# ── 3. self-declare: source + fingerprint, never the full key ─────────────
# The fingerprint is the first 6 characters (the provider's fixed prefix, plus
# for some issuers a few key characters), the length, and a sha256 prefix —
# enough to identify the key across receipts without printing it. The full
# value is never printed; a value shorter than 20 characters is redacted and
# its hash is withheld too, because `len` plus a 48-bit digest filter brutes
# the HIDDEN characters: at 12 characters that is 6 hidden from a 62-char
# alphabet (≈5.7e10 — hours of GPU), exactly the oracle the redaction exists to
# deny. 20 leaves 14 hidden (≈1.2e25) and still covers every key in use here
# (the shortest is 30+). These lines are meant to be pasted into receipts, so
# the margin is deliberate.
# A run whose strip proof FAILED must not present itself as sanitized. The text
# is chosen here, by assignment (not shadowable), so the header stays true on the
# one path where the abort could not replace this process (a shadowed `exec`).
if [ -n "${_RWEK_ABORTED:-}" ]; then
  _RWEK_STRIP_STATE='provider keys: NOT SANITIZED, the ambient strip or the .env load did not take'
else
  _RWEK_STRIP_STATE='provider keys: ambient stripped'
fi
if [ -L "$_RWEK_ENV_FILE" ]; then
  # The symlink ALIAS is what the wrapper opens (so the label stays `.env`),
  # but the bytes come from its target — disclose the target, or a receipt
  # could claim the evals key while a different file (e.g. a fleet key file)
  # was actually read.
  printf '[eval-keys] %s; file=%s -> %s\n' \
    "$_RWEK_STRIP_STATE" "$_RWEK_ENV_FILE" "$(resolve_target "$_RWEK_ENV_FILE")" >&2
else
  printf '[eval-keys] %s; file=%s\n' \
    "$_RWEK_STRIP_STATE" "$_RWEK_ENV_FILE" >&2
fi
for _rwek_key in "${_RWEK_MANAGED_KEYS[@]}"; do
  if [ -z "${!_rwek_key+x}" ]; then
    printf '[eval-keys] %s source=unset fingerprint=none\n' "$_rwek_key" >&2
    continue
  fi
  _rwek_value=${!_rwek_key}
  # In a run whose strip proof failed, the env file is NOT the attested source
  # of a key this launcher did not itself read from that file — say so for that
  # key only, so the line for a key that DID come from the file stays true.
  _rwek_label=$_RWEK_SOURCE_LABEL
  case "${_RWEK_ABORTED:-}" in
    '')
      ;;
    *)
      case " ${_RWEK_FROM_FILE:- } " in
        *" $_rwek_key "*) ;;
        *) _rwek_label='<aborted: this run was not sanitized, source not attested>' ;;
      esac
      ;;
  esac
  if [ "${#_rwek_value}" -ge 20 ]; then
    _rwek_fp="${_rwek_value:0:6}…"
    _rwek_hash=$(sha256_of "$_rwek_value")
  else
    _rwek_fp="<redacted>"
    _rwek_hash="redacted"
  fi
  printf '[eval-keys] %s source=%s fingerprint=%s len=%s sha256=%s\n' \
    "$_rwek_key" "$_rwek_label" "$_rwek_fp" "${#_rwek_value}" \
    "$_rwek_hash" >&2
done

# ── 4. exec the command ───────────────────────────────────────────────────
# `exec` is the whole point — the wrapped command must replace this process, so
# its stdout/stderr/exit status are its own — and a caller CAN shadow it with an
# exported `BASH_FUNC_exec%%`. Without a sentinel that shadow is a silent no-op:
# a receipt is printed, the command never runs, the launcher exits 0. The line
# after `exec` is unreachable whenever `exec` worked (an exec'd process does not
# come back, and a FAILED exec makes a non-interactive bash exit by itself), so
# reaching it can only mean the builtin was shadowed. The sentinel is an
# absolute-path `"$BASH" -p -c …` (not a function lookup, and a child that
# imports no exported functions), so it reports the failure and returns 3 even
# when the caller also shadowed `printf`/`exit`.
exec "$@"
"$_RWEK_BASH" -p -c 'printf "[eval-keys] FATAL: exec did not replace this process, the command did NOT run\n" >&2; exit 3'
