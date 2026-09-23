#!/usr/bin/env bash
# run-with-eval-keys.sh — run an eval/measurement command against the REPO
# `.env` provider keys, deterministically and self-declared (#2718 / #4860).
#
# WHY THIS EXISTS
# ---------------
# The repo's `.env` loader (`tortoise/mcp_server.py::_load_dotenv`) only fills
# keys that are ABSENT — "never override an explicitly set (even empty)
# environment variable". So any ambient LLM key in the calling shell (this
# fleet sources `~/pi-keys.env`) BEATS the repo `.env`. On 2026-09-23 the
# sealed #2552 write-path measurement silently billed the ambient fleet
# OpenRouter key (which was exhausted: `limit=100, limit_remaining=0`) and
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
#   3. Prints a source + non-revealing fingerprint line for each managed key,
#      so the key a run used is knowable from its output. Paste the lines into
#      the run receipt; they never contain key material.
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

# The provider keys this wrapper owns — exactly the set the extraction/reader
# paths actually read:
#   tortoise/ingest.py::_PROVIDERS          → OPENROUTER / DEEPSEEK / OPENAI / GEMINI
#   tortoise/analyze.py::_LLM_PROVIDERS     → DEEPSEEK / OPENAI
#   tortoise/model_adapters.py              → VENICE (ask + longmem lanes)
# ANTHROPIC_API_KEY is deliberately NOT managed: no tortoise provider reads it
# (hosted_api.py::_llm_provider_keys documents the same exclusion), so managing
# it would only mask unrelated host noise.
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
absolute_path() {
  local p=$1 d b
  d=$(dirname -- "$p")
  b=$(basename -- "$p")
  if [ -d "$d" ]; then
    (CDPATH= cd -- "$d" && printf '%s/%s' "$(pwd)" "$b")
  else
    printf '%s' "$p"
  fi
}

# The declaration label. `.env` means the repo-root file and is used ONLY when
# EVAL_KEYS_ENV_FILE is unset (then ENV_FILE IS the repo-root .env). With the
# seam set, the absolute resolved path is printed instead — a relative seam
# value like `.env` from a foreign cwd must never be labelled `.env`, which
# would be false provenance in exactly the way this tool exists to prevent.
if [ -n "${EVAL_KEYS_ENV_FILE:-}" ]; then
  ENV_FILE=$(absolute_path "$ENV_FILE")
  SOURCE_LABEL=$ENV_FILE
else
  SOURCE_LABEL=.env
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

# ── 3. self-declare: source + fingerprint, never key material ─────────────
# The first 6 characters of a provider key are a shared, non-secret prefix
# (`sk-or-`, `sk-…`); the sha256 prefix carries the identity. A short value is
# redacted entirely rather than printed in full.
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
