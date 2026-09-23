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

set -u

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
MANAGED_KEYS=(
  OPENROUTER_API_KEY
  DEEPSEEK_API_KEY
  VENICE_API_KEY
  OPENAI_API_KEY
  GEMINI_API_KEY
)

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
ENV_FILE=${EVAL_KEYS_ENV_FILE:-$REPO_ROOT/.env}

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

SCRIPT_PATH=$(resolve_self "$0")
SCRIPT_DIR=$(dirname -- "$SCRIPT_PATH")
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)
ENV_FILE=${EVAL_KEYS_ENV_FILE:-$REPO_ROOT/.env}

# The declaration label. `.env` is used ONLY when the resolved env file IS the
# resolved repo-root `.env`; otherwise the absolute path is printed, so the
# label never names a file the wrapper did not read.
if [ -n "${EVAL_KEYS_ENV_FILE:-}" ]; then
  ENV_FILE=$(absolute_path "$ENV_FILE")
fi
if [ "$(absolute_path "$ENV_FILE")" = "$REPO_ROOT/.env" ]; then
  SOURCE_LABEL=.env
else
  SOURCE_LABEL=$ENV_FILE
fi

if [ "$#" -eq 0 ]; then
  printf 'usage: %s <command> [args...]\n' "$(basename -- "$0")" >&2
  exit 2
fi

is_managed_key() {
  local k=$1 m
  for m in "${MANAGED_KEYS[@]}"; do
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
unset "${MANAGED_KEYS[@]}"

# ── 2. load `.env` — explicit override for MANAGED keys, fill-if-absent for
#      everything else (never clobber an explicit TORTOISE_DB_URI). The
#      parsing semantics mirror `mcp_server._load_dotenv`: a leading `export `
#      is tolerated, blank/# lines are skipped, quoted values are literal, and
#      an unquoted value loses a ` #` inline comment while a bare `#` survives.
if [ -r "$ENV_FILE" ]; then
  while IFS= read -r raw || [ -n "$raw" ]; do
    line=$(trim "${raw%$'\r'}")
    case "$line" in
      '' | '#'*) continue ;;
    esac
    case "$line" in
      'export '*) line=${line#export } ;;
    esac
    case "$line" in
      *=*) ;;
      *) continue ;;
    esac

    key=$(trim "${line%%=*}")
    value=$(trim "${line#*=}")

    # Only well-formed shell/variable identifiers may reach `export`/`eval`.
    case "$key" in
      '' | [0-9]* | *[!A-Za-z0-9_]*) continue ;;
    esac

    first=${value:0:1}
    last=${value: -1}
    if { [ "$first" = '"' ] || [ "$first" = "'" ]; } && [ "$last" = "$first" ]; then
      # mirrors _load_dotenv: bare quotes are literal, both ends stripped; a
      # lone quote char yields an empty value (Python's [1:-1] clamps)
      if [ "${#value}" -ge 2 ]; then
        value=${value:1:${#value}-2}
      else
        value=""
      fi
    else
      case "$value" in
        *' #'*) value=$(trim "${value%% #*}") ;;
      esac
    fi

    if is_managed_key "$key"; then
      export "$key=$value"
    else
      eval "already=\${$key+x}"
      if [ -z "$already" ]; then export "$key=$value"; fi
    fi
  done <"$ENV_FILE"
else
  printf '[eval-keys] WARNING: %s not found — no provider key loaded from .env (fail-closed)\n' \
    "$ENV_FILE" >&2
fi

# ── 3. self-declare: source + fingerprint, never the full key ─────────────
# The fingerprint is the first 6 characters (the provider's fixed prefix, plus
# for some issuers a few key characters), the length, and a sha256 prefix —
# enough to identify the key across receipts without printing it. The full
# value is never printed, and a value shorter than 12 characters is redacted
# entirely.
printf '[eval-keys] provider keys: ambient stripped; file=%s\n' "$ENV_FILE" >&2
for key in "${MANAGED_KEYS[@]}"; do
  eval "present=\${$key+x}"
  if [ -z "$present" ]; then
    printf '[eval-keys] %s source=unset fingerprint=none\n' "$key" >&2
    continue
  fi
  eval "value=\${$key}"
  if [ "${#value}" -ge 12 ]; then
    fp="${value:0:6}…"
  else
    fp="<redacted>"
  fi
  printf '[eval-keys] %s source=%s fingerprint=%s len=%s sha256=%s\n' \
    "$key" "$SOURCE_LABEL" "$fp" "${#value}" "$(sha256_of "$value")" >&2
done

# ── 4. exec the command ───────────────────────────────────────────────────
exec "$@"
