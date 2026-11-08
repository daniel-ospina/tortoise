#!/bin/bash
# ── Tortoise Hosted API Entrypoint ───────────────────────────────
# Resolves TORTOISE_DB_URI at runtime. Priority:
#   1. TORTOISE_DB_URI (already set — explicit override)
#   2. FALKORDB_CLOUD_URI (FalkorDB Cloud managed instance — production)
#   3. embedded redislite (local/dev)
#
# FalkorDB Cloud (managed) is the ONLY production database. Its platform backups
# are 12-hourly with 7-day retention and restore-creates-a-NEW-instance; there is
# NO point-in-time recovery, and AOF availability on our tier is undocumented
# (the vendor's docs contradict each other — see #2881). Do not claim durability
# this deployment does not have: the in-app sweep was inert for 33 days and the
# watcher that should have alarmed never started (#2790, #2922). The former
# self-hosted falkordb-tortoise sidecar (AOF off, no backups) caused the
# 2026-08-05 data-loss incident and has been removed. If the cloud URI is
# missing in production, fail loudly rather than silently degrading.
# ──────────────────────────────────────────────────────────────────

set -euo pipefail

# #2923: never print a connection string — it carries the DB password. Scheme,
# host and path stay visible so the target is still diagnosable; userinfo is
# replaced by the canonical mask shape (`scheme://:***@host`, the same shape
# tortoise/__main__.py::_mask_uri_userinfo emits).
#
# Boundary rule: the LAST '@' in the string — NOT the first. A password may
# contain '@', '/', '?' or '#', and a rule that stops at the first delimiter of
# any kind can log the credential's tail. Masking to the last '@' also survives
# a value with stray leading whitespace (a real shape: a copy-pasted secret),
# and errs toward masking more rather than less.
#
# Trade-off on exotic shapes: masking to the LAST '@' can over-reach when an '@'
# appears after the authority (`rediss://host:1/db?u=a@b` → `rediss://:***@b`),
# which loses the host name for that line. That is diagnosability loss, never a
# leak, and real FalkorDB/Redis URIs put no '@' outside the userinfo.
#
# Scope: a single bare URI, which is all this entrypoint ever prints. The
# canonical Python helper (`tortoise/__main__.py::_mask_uri_userinfo`) additionally
# masks every `scheme://` occurrence inside a longer message; the shell helper is
# `^`-anchored and does not, by design. tests/test_boot_regressions.py pins the
# two to identical output over the corpus it enumerates.
#
# #2987 — redaction is PER LINE, and fail-closed is PER LINE. The rule above was
# applied to the whole value and then the fail-closed guard compared the whole
# value, so (1) sed, being line-oriented, masked only the line carrying the
# scheme and echoed the rest, and (2) the guard never fired because line 1 had
# already changed the comparison. A line is the unit the boot log emits, so a
# line must be safe on its own.
#
# #2987 second residual — the guard's only userinfo marker was '@', but '@' is
# not the only credential shape. A copy-paste that drops the '@host' tail leaves
# `scheme://:pw` (empty user) or `scheme://user:pw`; a copy-paste that keeps the
# port leaves `scheme://user:pw:6379`. The fail-closed predicate below mirrors
# `tortoise/__main__.py::_credential_shaped` exactly (see the parity test), and a
# line may only stay unmasked when it is a RECOGNISED-SAFE shape: empty, a plain
# host with no ':', a single-':' host:port with a NUMERIC port, or a bracketed
# IPv6 host with an optional numeric port. Everything else fails closed.
#
# #5267 — the cut set is not only whitespace, so the boundary the predicate
# judges at is only the fragment UP TO the first delimiter, and everything
# beyond it used to be echoed: `rediss://u:pw@h:1 :S3nPw`, `...@h:1":S3nPw`,
# `...@h:1#:S3nPw`, `...@host\r:S3nPw` (a lone CR is not a line break here —
# `read` splits on LF), `rediss:// :S3nPw/db`. Every delimiter-bounded fragment
# beyond the region is now judged by `_fragment_tell` (below), and a scheme-less
# continuation line is judged the same way when the VALUE carries a scheme
# (`user:S3nPw:6379` — the port-retaining copy-paste). The tell is deliberately
# NARROWER than the authority predicate: prose is full of `key: value` shapes
# (`error:`, `12:30:45`, `warn: bad`, `C:\foo`, a timestamp), and failing closed
# on those would drop the diagnostic the mask exists to preserve.
_redact_uri() {
    # ⛔ BYTE/ASCII SEMANTICS FOR THE WHOLE REDACTOR — this is a PARITY
    # contract, not a preference. The canonical helper in
    # `tortoise/__main__.py` is restricted to ASCII on purpose (`_scheme_ok`
    # restricts to ASCII "to match entrypoint.sh's sed class
    # `[a-zA-Z][a-zA-Z0-9+.-]*` — `str.isalpha()` alone accepts Unicode
    # letters, which the shell does not"), and `_authority_region` spells
    # its cut set out rather than using `[[:space:]]` "so it is
    # locale-independent". Both of those claims are FALSE without this line:
    # bracket RANGES and character CLASSES are collation-sensitive, so under
    # a UTF-8 locale they admit code points the ASCII predicate rejects.
    # Measured, under `LC_ALL=en_US.UTF-8`:
    #   `[!0-9]`   did NOT reject U+0660 — under a UTF-8 locale bash reads the
    #                                Arabic-Indic digit as INSIDE the collated
    #                                0-9 range, so `*[!0-9]*` does not match
    #                                and the port was read as numeric and the
    #                                value echoed, while the canonical failed
    #                                closed. (Under `C` the same `case` DOES
    #                                match and rejects it — that inversion is
    #                                the whole defect.)
    #   `sed -E s/^[a-zA-Z]$/…/` MATCHED `é` → `rédiss://user:pw@host:6379`
    #                                masked as a scheme while the canonical
    #                                failed closed
    #   `[[:alpha:]]` MATCHED `é` (and `[[:upper:]]` `É`) under a UTF-8
    #                                locale, and neither matches under `C`.
    #
    # ⛔ RANGES ARE NOT ONE THING — do not generalise this to "ranges are
    # collated". Measured on this host (bash 5.2.21, glibc en_US.utf8), as the
    # behaviour of each CONSTRUCT on `é` / U+0660:
    #   bash `case` class  `[[:alpha:]]`  MATCHES `é` under a UTF-8 locale,
    #                                     not under `C`      -> COLLATED
    #   bash `case` range  `[a-zA-Z]`     does NOT match `é` in any locale
    #                                     (bash's `globasciiranges` is on by
    #                                     default)          -> NOT collated
    #   bash `case` range  `[0-9]`        treats U+0660 as a DIGIT under a
    #                                     collating UTF-8 locale -> COLLATED
    #   sed ERE range      `[a-zA-Z]`     MATCHES `é` under `en_US.UTF-8`, not
    #                                     under `C` or `C.UTF-8` -> COLLATED
    # So the pin is needed for three different reasons, not one, and `C.UTF-8`
    # is NOT a substitute for `C` on the sed path. (NBSP is NOT an example here:
    # `[[:space:]]` does not match U+00A0 in bash `case`, `sed` or `grep` on
    # glibc — measured.)
    #
    # ⛔ BOTH LINES BELOW ARE REQUIRED — `local` IS NOT `export`. `local`
    # restores the caller's locale on return, but it only PRESERVES the export
    # attribute of an existing export; it never CONFERS one. With `LC_ALL` in
    # the ambient environment the `sed` child inherits `LC_ALL=C`. With only
    # `LANG=en_US.UTF-8` and `LC_ALL` ABSENT — a developer shell, and many
    # container images — a bare `local LC_ALL=C` leaves the pin a plain shell
    # variable and the child keeps UTF-8 collation. Measured on GNU bash
    # 5.2.21: in that shape `rédiss://user:S3ntinel@host:6379` was masked as a
    # scheme while the canonical helper failed closed on it, so the parity
    # contract was still open on exactly the hosts the pin exists to protect.
    # `export LC_ALL` closes it and exports the LOCAL value, so the caller's
    # `LC_ALL` is still restored on return (verified: `en_US.UTF-8` in, `C`
    # inside, `en_US.UTF-8` out; unset in, unset out). Do not remove either
    # line to "keep the locale" — the redactor must behave identically on
    # every runner.
    # Regression: `tests/test_boot_regressions.py::test_the_shell_redactor_is_locale_independent`
    # runs BOTH environments (`lc_all-set` and `lang-only`) across three
    # locales (#9978).
    local LC_ALL=C
    export LC_ALL
    local uri="${1:-}" masked line out="" first=1 shaped=0 candidate \
          tail before rest cut=$'[/?#"\047 \t\v\f\r\n]' \
          region scan frag uri_has_scheme=0
    if [ -z "$uri" ]; then
        return 0
    fi
    # #5267: a scheme-less line is judged as credential material only when the
    # VALUE carries a scheme — `user:pw:6379` is a continuation of a URI value
    # split across lines, while the same text alone (prose, a path) is not.
    # Mirrors `_uri_bearing` in the canonical helper.
    case "$uri" in
        *://*) uri_has_scheme=1 ;;
    esac
    # #5267 — the fragment tell. The authority region is bounded at the first
    # delimiter, so a credential in a LATER delimiter-bounded fragment was never
    # judged (`rediss://u:pw@h:1 :S3nPw`, `...@host\r:S3nPw`,
    # `rediss:// :S3nPw/db`). The authority predicate cannot judge a fragment:
    # it fails closed on any `key: value` (`error:`, `C:\foo`, `connect:`),
    # which is prose the boot log must keep printing. Two tells are unambiguous,
    # and mirror `tortoise/__main__.py::_credential_tell` exactly:
    #   * ':'-led with a non-empty tail (`:S3nPw`) — the empty-user form of a
    #     dropped '@host', a shape no prose takes;
    #   * `user:pw:6379` — two-or-more ':' with an ALL-DIGIT last field AND at
    #     least one ASCII letter. The letter requirement is what separates the
    #     credential from the prose that shares its arity (a bare timestamp
    #     `12:30:45` has no letter; a single colon, `warn: bad`, is excluded).
    _fragment_tell() {
        case "$1" in
            :?*) return 0 ;;
        esac
        case "$1" in
            *:*:*)
                case "$1" in
                    *[a-zA-Z]*) ;;
                    *) return 1 ;;
                esac
                # `[!0-9]` is a COLLATION-SENSITIVE range: under a UTF-8
                # locale (measured: en_US.UTF-8) bash matches U+0660 and the
                # other Arabic-Indic digits inside `0-9`, so `host:٠` read as a
                # numeric port and the fragment was accepted while the
                # canonical helper refused it. The explicit character LIST is
                # not collated, so it is ASCII-only in every locale — which is
                # the contract `_mask_uri_userinfo` documents. Do not "tidy"
                # this back into a range.
                case "${1##*:}" in
                    ''|*[!0123456789]*) return 1 ;;
                esac
                if [ -n "${1%%:*}" ]; then
                    return 0
                fi
                ;;
        esac
        return 1
    }
    while IFS= read -r line || [ -n "$line" ]; do
        if ! masked=$(printf '%s' "$line" | sed -E 's|^([[:space:]]*[a-zA-Z][a-zA-Z0-9+.-]*://).*@|\1:***@|'); then
            # A redaction failure must not silently blank the target either:
            # without this the line reads "→ " and the diagnosability the
            # redaction exists to preserve is gone with no signal.
            masked="<unprintable-uri>"
        else
            # Whether sed masked anything decides WHICH text is the candidate to
            # judge. MASKED (the line carried an '@'): the text after the LAST
            # '@' is the host for a well-formed URI, but a malformed value can
            # carry a credential there and this used to echo it verbatim —
            # `rediss://u:pw@h:1 rediss://:pw` (a later scheme:// this
            # ^-anchored helper cannot reach at all) and `rediss://u:pw@host:pw`
            # (a credential-shaped tail; the SAME region fails closed below when
            # no '@' is present — that asymmetry was the leak). The shared
            # predicate judges it. This reverses the #720 review's "bad port
            # stays readable" behaviour: `...@127.0.0.1:notaport` and `...@[abc`
            # now fail closed, and the port value survives in the exception
            # text of the same message. UNMASKED: judge the post-scheme /
            # continuation text, as before.
            shaped=0
            candidate=""
            if [ "$masked" != "$line" ]; then
                candidate="${line##*@}"
                case "$candidate" in
                    *://*) shaped=1 ;;
                esac
            else
                case "$line" in
                    *@*) shaped=1 ;;
                esac
            fi
            if [ "$shaped" -eq 0 ]; then
                if [ "$masked" = "$line" ] && [ "$line" != "${line#*://}" ]; then
                    candidate="${line#*://}"
                    # A single DB URI has one '://'. A SECOND '://' on a line
                    # with no '@' cannot be predicated by this ^-anchored
                    # helper without the embedded-URI handling the canonical
                    # has, and a later `scheme://:pw` / `scheme://user:pw`
                    # would otherwise ride through while the canonical masks it.
                    # Fail closed rather than echo a later occurrence.
                    if [ "$candidate" != "${candidate#*://}" ]; then
                        shaped=1
                    fi
                else
                    # No scheme on this line. Only the empty-user tell is safe
                    # here: an arbitrary 'a:b' line is prose or a path, not a
                    # credential, and the canonical helper sees such text in
                    # error messages. A continuation line of a multi-line
                    # credential is exactly `:password`.
                    case "$line" in
                        :*) candidate="$line" ;;
                    esac
                fi
                # Bound the authority at the first path/query/fragment
                # delimiter, quote or ASCII whitespace: everything after it is
                # not the authority. The set is spelled out rather than
                # `[[:space:]]` so it is locale-independent and byte-identical
                # to `tortoise/__main__.py::_authority_region`. Keep the two in
                # sync — a different cut set is a divergence. (`[[:space:]]`
                # is NOT additionally matching NBSP here: measured on glibc it
                # does not, in bash `case`, `sed` or `grep`. The literal set
                # is still the right choice — it is byte-exact and needs no
                # argument about what a class means under a given locale.)
                region="${candidate%%$cut*}"
                # #5267: everything AFTER the bounded region is delimiter-
                # bounded fragment material too, judged by the tell above —
                # the canonical judges the same text in the same place. A
                # scheme-less line is that material in full (its region is the
                # line's own first fragment, predicated just below).
                scan="${candidate#"$region"}"
                if [ "$uri_has_scheme" -eq 1 ] && [ -z "$candidate" ]; then
                    scan="$line"
                fi
                while [ -n "$scan" ]; do
                    frag="${scan%%$cut*}"
                    if [ -n "$frag" ] && _fragment_tell "$frag"; then
                        shaped=1
                        break
                    fi
                    if [ "$frag" = "$scan" ]; then
                        break
                    fi
                    scan="${scan#*$cut}"
                done
                if [ "$shaped" -eq 0 ] && [ -n "$region" ]; then
                    case "$region" in
                        \[*)
                            # A bracketed IPv6 host is recognised-safe with an
                            # optional numeric port; a malformed bracket is not.
                            tail="${region#*\]}"
                            case "$tail" in
                                '') ;;
                                :*)
                                    # `[::1]:6379` is recognised-safe; the
                                    # port must be ALL digits. `:[0-9]*`
                                    # accepted a digit-led non-numeric suffix
                                    # (`:[0-9]*` matches `:6379:S3n`), so a
                                    # bracketed credential failed OPEN here
                                    # while the canonical masked it.
                                    #
                                    # The explicit list, not `[!0-9]`: a range
                                    # is collation-sensitive and admits the
                                    # Arabic-Indic digits under a UTF-8 locale
                                    # (see `_fragment_tell`).
                                    case "${tail#:}" in
                                        ''|*[!0123456789]*) shaped=1 ;;
                                    esac
                                    ;;
                                *) shaped=1 ;;
                            esac
                            ;;
                        *:*)
                            before="${region%%:*}"
                            rest="${region#*:}"
                            if [ -z "$before" ] || [ "$rest" != "${rest#*:}" ]; then
                                # empty user before the first ':' (a dropped
                                # '@host'), or more than one ':' — a genuine
                                # host:port has exactly one.
                                shaped=1
                            else
                                # A NUMERIC field after the ':' is a port, not a
                                # credential, so a password-less target keeps
                                # printing unchanged. An empty or non-numeric
                                # tail is credential material.
                                #
                                # The explicit list, not `[!0-9]`: a range is
                                # collation-sensitive and admits the
                                # Arabic-Indic digits under a UTF-8 locale
                                # (see `_fragment_tell`).
                                case "$rest" in
                                    ''|*[!0123456789]*) shaped=1 ;;
                                esac
                            fi
                            ;;
                    esac
                fi
            fi
            if [ "$shaped" -eq 1 ]; then
                masked="<uri-redacted-unrecognised-shape>"
            fi
        fi
        if [ "$first" -eq 1 ]; then
            first=0
            out="$masked"
        else
            out+=$'\n'"$masked"
        fi
    done <<< "$uri"
    printf '%s' "$out"
}

# #1349 T11: reject the benchmark-only probe seam in the hosted image.
# TORTOISE_EMBEDDER_OVERRIDE is the marker of tools/embedder_probe.py's
# inject_model — a harness mechanism for candidate-model benchmark runs
# ONLY. Production must never honor it (a container carrying it is
# misconfigured by construction; serving a wrong embedder silently would be
# the #1349 class of incident the swap exists to prevent). Guard FIRST so
# the error is unambiguous even if the model cache is also missing.
if [ -n "${TORTOISE_EMBEDDER_OVERRIDE:-}" ]; then
    echo "tortoise: FATAL — TORTOISE_EMBEDDER_OVERRIDE is set (${TORTOISE_EMBEDDER_OVERRIDE})" >&2
    echo "tortoise: the embedder probe is benchmark-only and must never reach the hosted image — unset it" >&2
    exit 1
fi

# Embedding model cache — pre-downloaded at build time (Dockerfile ENV).
# Export defensively so it propagates even if the entrypoint is run outside
# the container image.
export HF_HOME="${HF_HOME:-/app/model}"
export SENTENCE_TRANSFORMERS_HOME="${SENTENCE_TRANSFORMERS_HOME:-/app/model}"

# #1726 (Slice 1): the GitHub-docs ingest sandbox — a SERVER-OWNED staging
# dir (docs are staged under {TORTOISE_INGEST_BASE_DIR}/{org_id}/).
# /data is the persistent Fly volume (fly.toml mounts) — a restart keeps
# the staged corpus so re-runs stay 0-new (hash dedup); the dir is
# server-owned, so the #236 user-supplied-path exclusion is untouched (the
# tenant path never passes a user path). Fail-closed: /v1/index/docs
# refuses to run without this set.
export TORTOISE_INGEST_BASE_DIR="${TORTOISE_INGEST_BASE_DIR:-/data/ingest}"

# #4240: the hosted lane's per-graph JSONL REBUILD journal. Off-box durability
# is unchanged (docs/durability-posture.md — the JSONL is a domain event log,
# never the durability mechanism); this makes the derived graph
# `replay(journal)` so `rebuild_all` can reconstruct the capture instead of
# silently losing its live-only aboutObject edges. /data is the persistent Fly
# volume (fly.toml mounts), one file per graph under
# {TORTOISE_EVENT_LOG_BASE_DIR}/{graph}/events.jsonl.
export TORTOISE_EVENT_LOG_BASE_DIR="${TORTOISE_EVENT_LOG_BASE_DIR:-/data/events}"

# Fast-fail if the pre-downloaded model cache is missing (code-review P3, #160)
# — the build-time bake in Dockerfile.hosted is the ONLY source; a missing
# cache means a broken image, not a retryable condition. #1349: the bake is
# org-qualified BAAI/bge-small-en-v1.5 (EMBEDDING_MODEL) — the cache dir must
# match the model the image bakes.
if [ ! -d "${HF_HOME}/models--BAAI--bge-small-en-v1.5" ]; then
    echo "tortoise: FATAL — embedding model cache not found at ${HF_HOME}" >&2
    echo "tortoise: expected ${HF_HOME}/models--BAAI--bge-small-en-v1.5" >&2
    exit 1
fi

# Embedding model pre-warm moved into the app: the FastAPI lifespan
# (tortoise/hosted_api.py _lifespan) spawns a daemon-thread pre-warm, so
# uvicorn binds 0.0.0.0:8000 IMMEDIATELY and /health passes on cold start.
#
# Previous behavior (fail-fast blocking pre-warm, #160) crashed deploys:
# EmbeddingModel._LOAD_TIMEOUT_S (30s) is shorter than a cold 2-core/2GB VM
# needs to import torch + sentence-transformers + load the ~130MB model, so
# get() returned None, the assert failed, the entrypoint exited 1, uvicorn
# never started, and the Fly health check timed out (issue #545).
#
# Embeddings are OPTIONAL — FTS + structural RRF work without them
# (embeddings.py docstring). The model cache existence check above still
# catches a broken image (missing bake) cheaply; a present-but-corrupt bake
# is surfaced by the post-pre-warm model-identity log in hosted_api.py
# (#1349 T10, non-blocking).
_IS_SERVER=0
for _arg in "$@"; do
    if grep -q "uvicorn" <<<"$_arg"; then
        _IS_SERVER=1
        break
    fi
done

if [ "$_IS_SERVER" = "1" ]; then
    # #1726 (Slice 1): pre-create the server-owned ingest sandbox (fail-closed
    # at startup if the persistent volume is missing/unwritable). Only in
    # server mode — the release/guard invocations never mount /data.
    mkdir -p "$TORTOISE_INGEST_BASE_DIR"
    # #4240: pre-create the journal base dir beside the ingest sandbox — it is
    # on the same persistent volume and a missing/unwritable volume is a
    # misconfigured machine, not a retryable condition. #4240 review F1: the
    # check must be WRITABILITY, not creation — `mkdir -p` succeeds on a dir
    # that already exists but is not writable. What this probe covers, stated
    # exactly (F1 cycle 2 — the earlier comment overclaimed):
    #   * a NON-ROOT mode-bit failure: the dir exists at mode 500 and the probe
    #     write is refused for every uid that is not root;
    #   * EROFS/ENOSPC on ANY uid, root included — root cannot bypass those.
    # It does NOT cover a mode-bit failure when the process is root, and the
    # image sets NO USER (Dockerfile.hosted), so on Fly the server is uid 0 and
    # `: > .write-probe` SUCCEEDS on a mode-0500 dir. That residual is NOT
    # silent, it is just not a boot-time signal: a failed append logs at ERROR,
    # increments tortoise_journal_write_failures_total and is disclosed on the
    # capture receipt (sdk.py::TortoiseSDK._emit_event). A per-record append
    # failure is fail-soft by design (the graph mutation stands), so this boot
    # probe is the only place a boot-time VOLUME misconfig can fail LOUD.
    mkdir -p "$TORTOISE_EVENT_LOG_BASE_DIR"
    if ! : > "$TORTOISE_EVENT_LOG_BASE_DIR/.write-probe" 2>/dev/null; then
        echo "tortoise: FATAL — TORTOISE_EVENT_LOG_BASE_DIR is not writable: $TORTOISE_EVENT_LOG_BASE_DIR" >&2
        echo "tortoise: the hosted lane's journal cannot record — its aboutObject edges would be live-only and a rebuild would lose them." >&2
        exit 1
    fi
    rm -f "$TORTOISE_EVENT_LOG_BASE_DIR/.write-probe"
    echo "tortoise: uvicorn server — embedding pre-warm runs in-app (non-blocking, degraded-but-alive)"
else
    echo "tortoise: skipping embedding pre-warm (non-server command: release check)"
fi

if [ -n "${TORTOISE_DB_URI:-}" ]; then
    echo "tortoise: using explicit TORTOISE_DB_URI → $(_redact_uri "${TORTOISE_DB_URI}")"
elif [ -n "${FALKORDB_CLOUD_URI:-}" ]; then
    export TORTOISE_DB_URI="${FALKORDB_CLOUD_URI}"
    echo "tortoise: using FalkorDB Cloud (managed) → $(_redact_uri "${TORTOISE_DB_URI}")"
elif [ -n "${FLY_APP_NAME:-}" ]; then
    echo "tortoise: FATAL — FALKORDB_CLOUD_URI not set in production. Refusing to start with no durable DB." >&2
    exit 1
else
    echo "tortoise: using embedded redislite (local/dev mode)" >&2
fi

# ── #1677: multi-worker topology — MEASURE the count, then hand it to the app ──
# `Dockerfile.hosted` passes no `--workers`, so this deployment runs ONE uvicorn
# worker. Nothing makes that visible: uvicorn resolves its worker count as
# CLI flag -> $UVICORN_WORKERS -> $WEB_CONCURRENCY -> 1, so ANY actor that can set
# one Fly env var moves the machine to N workers with no code change and no
# signal.
#
# The REFUSAL is not here. It lives in `tortoise/worker_topology.py`, because it
# has to be reachable by a direct `uvicorn tortoise.hosted_api:app --workers N`
# too — an invocation that never runs this script. One implementation of the
# policy, one place to change it.
#
# What this block adds is the half those callers cannot know: the count uvicorn
# WILL run, measured from the CMD argv the way uvicorn resolves it, exported so
# the app's refusal can name the real number, and logged once at boot so an
# ambient WEB_CONCURRENCY stops being an invisible fact.
#
# ⚠️ `TORTOISE_WEB_WORKERS` is an OUTPUT of this script, never an operator input:
# uvicorn does not read it, so setting it by hand cannot raise the count.
# ── #1677: the worker-count measurement, from here to the gate sentinel ──
# ⛔ THIS LINE IS A TEST SENTINEL (tests/test_worker_topology.py lifts the block
# between it and the gate sentinel and EXECUTES it). Keep it directly above the
# first of the block's functions, and add any new helper INSIDE the block — a
# helper defined above this line is invisible to the lifted block, which is how a
# refactor once made the shell arm answer 1 for every input and only the pytest
# suite noticed.
_uvicorn_flag_option() {
    # ⛔ ONE list, because two copies drifted and the drift was a FAIL-OPEN.
    # `uvicorn.main.main.params` marks these `is_flag=True` (pinned 0.52.1): they
    # consume NO value, so the token after them is still a token. Every OTHER
    # long option consumes the next token as its value, which is what makes
    # `--host --workers=1` a host of "--workers=1" rather than a workers flag.
    # The count reader had its own copy of this list and omitted `--reload`, so
    # `--reload --workers 4` read as "no workers value" (the reloader swallowed
    # it) and the exported count fell through to `$WEB_CONCURRENCY` — caught by
    # `_AGREEMENT_TABLE`, which is exactly why both arms run one table.
    #
    # ⛔ The `--no-*` forms are FLAGS too. Five of uvicorn's booleans are declared
    # `"--x/--no-x"`, so click keeps the negated spelling in `secondary_opts` and
    # a table transcribed from `opts` alone reads `--no-access-log` as
    # VALUE-TAKING — it then swallows the next token, so `--no-access-log
    # --workers=4` reads as "no count" and the lifespan-off guard certifies ONE
    # worker while uvicorn serves FOUR ungated processes (MEASURED, no ambient
    # `$WEB_CONCURRENCY` needed), and `--no-access-log --reload` is misread as
    # "no reloader", refusing a legitimate dev topology. Pinned against the
    # parser's `opts` AND `secondary_opts` by
    # `test_the_uvicorn_flag_set_is_uvicorns_own`.
    case "$1" in
        --reload|--access-log|--no-access-log|--use-colors|--no-use-colors\
        |--proxy-headers|--no-proxy-headers|--server-header|--no-server-header\
        |--date-header|--no-date-header|--version|--reset-contextvars\
        |--factory) return 0 ;;
    esac
    return 1
}


_trim_edges() {
    # Strip surrounding whitespace from "$1" — LINE-AGNOSTIC, unlike
    # `sed -e 's/^[[:space:]]*//'`, which is line-oriented: a value whose LEADING
    # whitespace contains a newline is split into records, the first run is never
    # removed, and the count then fails the digit test and normalises to 1 while
    # Python (`str.strip`) and uvicorn (`int`) read the same value as N — measured:
    # `WEB_CONCURRENCY=$'\n2'` -> shell 1 / Python 2, and the pre-exec gate then
    # reads the script's own `1` and PERMITS a two-worker boot.
    #
    # Only bash pattern expansion is line-agnostic here; `[[:space:]]` matches a
    # newline inside a bracket expression, which is what makes this correct.
    local _v="$1"
    _v="${_v#"${_v%%[![:space:]]*}"}"
    _v="${_v%"${_v##*[![:space:]]}"}"
    printf '%s' "$_v"
}

_resolve_raw_requested_workers() {
    # uvicorn's own precedence: `--workers N` / `--workers=N` over the argv, else
    # $UVICORN_WORKERS (click's `auto_envvar_prefix` value for that flag), else
    # $WEB_CONCURRENCY, else 1. Both flag forms are handled because `--workers=N`
    # is what a hand-edited CMD usually carries.
    #
    # The RAW value, split out from `_resolve_requested_workers` below so the
    # `--lifespan off` guard can ask a DIFFERENT question of the same input —
    # "is this a value the shell can VERIFY?" rather than "what number is it?" —
    # without a second copy of this precedence chain.
    local _args=("$@") _i=0 _count="" _arg="" _skip=0
    # ⛔ ARITY, exactly as the reload scan below does it. A `--workers=1` token is
    # a workers FLAG only when click reaches it as a TOKEN; `--host --workers=1`
    # makes it `--host`'s VALUE, so uvicorn reads `$WEB_CONCURRENCY` and runs N
    # while a membership scan reports 1 — MEASURED on the pinned 0.52.1:
    # `--host --workers=1` (and `--uds` / `--root-path` / `--app-dir` /
    # `--forwarded-allow-ips` / `--loop` with it) leaves `Config.workers = 4`
    # under `WEB_CONCURRENCY=4`. That masked count then certified "one worker"
    # to the `--lifespan off` guard, which is the fail-open this whole block
    # exists to close.
    while [ "$_i" -lt "${#_args[@]}" ]; do
        _arg="${_args[$_i]}"
        if [ "$_skip" = "1" ]; then _skip=0; _i=$((_i + 1)); continue; fi
        case "$_arg" in
            --workers)
                _i=$((_i + 1))
                _count="${_args[$_i]:-}"
                ;;
            --workers=*)
                _count="${_arg#--workers=}"
                ;;
            --*=*) : ;;
            -*)
                # A long option with no inline value consumes the NEXT token as
                # its value — unless it is one of uvicorn's FLAGS.
                _uvicorn_flag_option "$_arg" || _skip=1
                ;;
        esac
        _i=$((_i + 1))
    done
    if [ -z "$_count" ]; then
        _count="${UVICORN_WORKERS:-}"
    fi
    if [ -z "$_count" ]; then
        _count="${WEB_CONCURRENCY:-1}"
    fi
    printf '%s' "$_count"
}

_count_is_verifiable() {
    # ⛔ TRUE only when the raw value is an ASCII-integer spelling this shell can
    # actually READ. `_resolve_requested_workers` normalises anything it cannot
    # parse to 1, and that normalisation is NOT a measurement: uvicorn's
    # `int($WEB_CONCURRENCY)` (`config.py:353`) also accepts non-ASCII decimal
    # digits and Unicode whitespace — MEASURED, `WEB_CONCURRENCY=$'\xc2\xa02'`
    # starts TWO children while both resolvers report 1. Harmless while the app
    # gate runs (the children are `supervised` and refused); NOT harmless with
    # the lifespan off, which is the only caller of this predicate.
    # ⛔ The locale pin is what makes "ASCII-integer spelling" TRUE rather than
    # hoped for: `[0-9]` is a collating range, so under `en_US.UTF-8` this same
    # regex accepts U+0661 U+0662 ("12") and ADMITS a boot that starts twelve
    # ungated workers. MEASURED on this host, both locales.
    local LC_ALL=C
    export LC_ALL
    local _raw
    _raw="$(_resolve_raw_requested_workers "$@")"
    _raw="$(_trim_edges "$(_resolve_raw_requested_workers "$@")")"
    case "$_raw" in
        +*) _raw="${_raw#+}" ;;
    esac
    [[ "$_raw" =~ ^[0-9]+(_[0-9]+)*$ ]]
}

_app_env_file_in_play() {
    # ⛔ uvicorn's FOURTH count input. `Config.__init__` runs
    # `load_dotenv(dotenv_path=env_file)` BEFORE it reads `$WEB_CONCURRENCY`
    # (`config.py:348-353`), so `--env-file` / click's `$UVICORN_ENV_FILE`
    # (`main.py:191-197`) can set the count AFTER this script measured it.
    # MEASURED on the pinned 0.52.1: `UVICORN_ENV_FILE=four.env`
    # (`WEB_CONCURRENCY=4`) with `UVICORN_LIFESPAN=off` started FOUR server
    # processes with the gate never entered, while this block reported one
    # worker. With an env file in play, NO count measured here is the count
    # uvicorn will run.
    for _arg in "$@"; do
        case "$_arg" in
            --env-file) return 0 ;;
            --env-file=*) return 0 ;;
        esac
    done
    [ -n "${UVICORN_ENV_FILE:-}" ]
}

_resolve_requested_workers() {
    # uvicorn's own precedence: `--workers N` / `--workers=N` over the argv, else
    # $UVICORN_WORKERS (click's `auto_envvar_prefix` value for that flag), else
    # $WEB_CONCURRENCY, else 1. Both flag forms are handled because `--workers=N`
    # is what a hand-edited CMD usually carries.
    # ⛔ Locale-pinned for the same measured reason as the two predicates below:
    # the `case`/`=~` classes on `_count` are COLLATING ranges, so under a UTF-8
    # locale they treat U+0661 as a digit and this function stops normalising a
    # value uvicorn's `int()` reads as 12.
    local LC_ALL=C
    export LC_ALL
    local _count
    _count="$(_resolve_raw_requested_workers "$@")"
    # Trim ENDS only (never interior whitespace — `1 2` is not 12, it is a value
    # uvicorn's `int()` REJECTS), then drop a leading `+`.
    #
    # Both are required for this measurement to agree with the server that
    # actually starts: uvicorn parses the flag and `$WEB_CONCURRENCY` through
    # `int()`, whose grammar is an optional sign, then digits, with surrounding
    # whitespace tolerated. Measured on the pinned uvicorn (0.52.1): `--workers +2` starts
    # TWO children and `WEB_CONCURRENCY=' 2 '` starts two as well. A digit-only
    # test rejected both and reported 1 — the measurement lying about the very
    # topology it exists to report, in the direction that reads as "safe".
    #
    # ⛔ The grammar below is `int()`'s own EXCEPT for non-ASCII decimal digits:
    # `int()` accepts `_` separators BETWEEN digits (`1_0` -> 10) and Unicode
    # decimals (`١٢` -> 12). The separators are reproduced here (bash `=~`, this
    # script is `#!/bin/bash`) so the number matches the server that starts;
    # Unicode digits are NOT (bash arithmetic cannot evaluate them), and stay a
    # DECLARED RESIDUAL in the under-report direction — pinned by
    # `_AGREEMENT_TABLE` in `tests/test_worker_topology.py`, which EXECUTES both
    # this block and `worker_topology._positive_int` on the same input, so the two
    # cannot drift. Do not "simplify" one arm without the other.
    _count="$(_trim_edges "$_count")"
    case "$_count" in
        +*) _count="${_count#+}" ;;
    esac
    # Strip PEP 515 separators FIRST, and only in the shape `int()` accepts:
    # `_10`, `10_` and `1__0` all raise ValueError there, so they must keep
    # falling through to the normalise-to-1 arm rather than being reinterpreted
    # as a count uvicorn would refuse to start.
    if [[ "$_count" =~ ^[0-9]+(_[0-9]+)*$ ]]; then
        _count="${_count//_/}"
    fi
    # `0`, a negative and a non-numeric value are not ours to reinterpret:
    # uvicorn treats `workers > 1` as the supervisor's switch (so `0` and `-2`
    # run in-process) and would raise on the non-numeric. Normalise them to 1 so
    # this measurement can never DISAGREE with the server that starts.
    case "$_count" in
        ''|*[!0-9]*) _count=1 ;;
    esac
    # `00`-style zero is still zero.
    if [ "$_count" -eq 0 ] 2>/dev/null; then _count=1; fi
    printf '%s' "$_count"
}

TORTOISE_WEB_WORKERS="$(_resolve_requested_workers "$@")"
export TORTOISE_WEB_WORKERS

# ── The count predicates are LOCALE-PINNED, and that pin is load-bearing ────
# `[0-9]` is a COLLATING RANGE, not a byte class: under a UTF-8 locale whose
# collation folds digits, `[[ "١٢" =~ ^[0-9]+$ ]]` is TRUE and
# `case … *[!0-9]*` does not match — MEASURED on this host:
#   LC_ALL=C            -> regex FALSE, `case` matches  -> the count is unreadable
#   LC_ALL=en_US.utf8   -> regex TRUE,  `case` misses   -> the count reads 12
# `int("١٢") == 12` too, so an unpinned pair AGREES on a topology nobody
# measured: with the lifespan off, uvicorn starts twelve ungated children while
# this block reports one. Same defect class, and the same pin, as `_redact_uri`'s
# note at the top of this file. The pin lives INSIDE each of the three helpers
# (`local LC_ALL=C; export LC_ALL`) rather than once here, because `local`
# restores the caller's value on return — a script-wide `export` would silently
# change every later command's behaviour, which is a bigger claim than the
# measurement supports (pinned by
# `test_the_count_predicates_are_LOCALE_PINNED`).

# ── Is the reloader going to serve instead of N workers? ──────────────────
# uvicorn IGNORES `--workers` while reloading — measured on the pinned 0.52.1:
# `--reload --workers 4` prints `"workers" flag is ignored when reloading is
# enabled` and starts exactly ONE server process. Reporting 4 here would be the
# fourth instance of the class this block exists to close (a measurement lying
# about the topology, in the direction that reads as "safe").
#
# Both of uvicorn's inputs for that flag are checked: the token, and click's
# `auto_envvar_prefix="UVICORN"` value `$UVICORN_RELOAD` — read through CLICK's
# vocabulary (`t`/`y` are TRUE there), which is the parity contract with
# `worker_topology._CLICK_TRUE`, pinned by `_AGREEMENT_TABLE`'s reload rows and
# `test_the_shipped_block_reports_one_worker_while_reloading`.
_reload_requested=0
# ⛔ ARITY, not token membership. MEASURED on the pinned 0.52.1:
# `--reload-include --reload --workers 2` makes `--reload` the INCLUDE PATTERN —
# uvicorn starts NO reloader ("Current configuration will not reload …") and TWO
# workers. A membership loop reads that as reloading and prints `workers = 1`,
# which is the fail-open class the gate exists to close. Every long option other
# than the FLAGS consumes the next token as its value, so that token is never
# re-read as a flag. The flag list lives in `_uvicorn_flag_option` — ONE list
# shared with the count reader, so a flag can never be a value-taking option in
# one loop and a flag in the other — and is pinned against
# `uvicorn.main.main.params` by `test_the_uvicorn_flag_set_is_uvicorns_own`.
_reload_skip=0
for _arg in "$@"; do
    if [ "$_reload_skip" = "1" ]; then _reload_skip=0; continue; fi
    case "$_arg" in
        --reload) _reload_requested=1 ;;
        --*=*) : ;;
        -*)
            # Same predicate the count reader uses — one list, so `--reload` can
            # never be a value-taking option in one loop and a flag in the other.
            _uvicorn_flag_option "$_arg" || _reload_skip=1
            ;;
    esac
done
case "$(printf '%s' "$(_trim_edges "${UVICORN_RELOAD:-}")" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on|t|y) _reload_requested=1 ;;
esac

# ── Will the app's LIFESPAN run at all? ─────────────────────────────────
# The multi-worker admission gate is the FIRST statement of
# `hosted_api._lifespan` (`tortoise/worker_topology.py`, #1677), so turning the
# lifespan off removes it completely: uvicorn starts N workers, none of them
# runs the gate, and on an embedded DB they open N DIVERGENT copies of one file.
# MEASURED on the pinned 0.52.1: `UVICORN_LIFESPAN=off WEB_CONCURRENCY=2` (no
# TORTOISE_DB_URI) served two server processes and the gate never ran.
#
# uvicorn's `<flag> --lifespan` is `type=LIFESPAN_CHOICES` = (auto, on, off),
# click `Choice` is case-SENSITIVE, and a CLI value beats the envvar — so an
# exact `off` from either source is the only disabling value that starts.
_app_lifespan_disabled() {
    local _want_value=0 _arg="" _value=""
    for _arg in "$@"; do
        if [ "$_want_value" = "1" ]; then _value="$_arg"; _want_value=0; continue; fi
        case "$_arg" in
            --lifespan=*) _value="${_arg#--lifespan=}" ;;
            --lifespan) _want_value=1 ;;
        esac
    done
    if [ -z "$_value" ]; then _value="${UVICORN_LIFESPAN:-}"; fi
    [ "$_value" = "off" ]
}

if [ "$_IS_SERVER" = "1" ]; then
    if [ "$_reload_requested" = "1" ]; then
        echo "tortoise: uvicorn workers = 1 (reload; --workers is ignored while reloading)"
    elif _app_lifespan_disabled "$@"; then
        # ⛔ With the lifespan off, NOTHING in the app can gate the topology, so
        # this block must refuse unless the count is PROVABLY one from inputs it
        # can see. Two ways it may not be:
        #   * `--env-file` / `$UVICORN_ENV_FILE` — uvicorn loads a dotenv AFTER
        #     this measurement, so the real count is unknown here (measured: four
        #     workers served under a banner that said one);
        #   * a raw count this shell cannot parse (Unicode digits/whitespace).
        if [ "$TORTOISE_WEB_WORKERS" -gt 1 ]; then
            _refuse_why="${TORTOISE_WEB_WORKERS} uvicorn workers"
        elif _app_env_file_in_play "$@"; then
            _refuse_why="a --env-file / \$UVICORN_ENV_FILE that sets the worker count AFTER this measurement"
        elif ! _count_is_verifiable "$@"; then
            _refuse_why="a worker count this boot script cannot read (the value is not an ASCII integer), which uvicorn's int() may still turn into N"
        else
            _refuse_why=""
        fi
        if [ -n "$_refuse_why" ]; then
            {
                echo "tortoise: REFUSING to start — the APP LIFESPAN IS DISABLED (--lifespan off / \$UVICORN_LIFESPAN=off) and this boot has ${_refuse_why}."
                echo "tortoise: the multi-worker admission gate is the first statement of hosted_api._lifespan (tortoise/worker_topology.py, #1677), so with the lifespan off it never runs — and N workers on an EMBEDDED database open N divergent copies of one file."
                echo "tortoise: fix: drop the lifespan override so the gate runs, or run ONE worker (unset WEB_CONCURRENCY / drop --workers / drop --env-file and set the count in the environment)."
            } >&2
            exit 1
        fi
        echo "tortoise: lifespan DISABLED (--lifespan off) — the multi-worker admission gate cannot run; the count is verified as ONE worker, so there is nothing to gate"
        echo "tortoise: uvicorn workers = 1"
    elif _app_env_file_in_play "$@"; then
        # Truthful, and not a second gate: the app's lifespan gate still runs here
        # and refuses the spawned children, so this is the REPORT being honest
        # about what it cannot see (`load_dotenv` happens after this measurement).
        echo "tortoise: uvicorn workers = UNVERIFIED — --env-file / \$UVICORN_ENV_FILE can set \$WEB_CONCURRENCY AFTER this measurement, so the count above is not the count uvicorn will run; the app's admission gate still refuses a multi-worker boot (tortoise/worker_topology.py, #1677)"
    elif [ "$TORTOISE_WEB_WORKERS" -gt 1 ]; then
        echo "tortoise: uvicorn workers = ${TORTOISE_WEB_WORKERS} — MULTI-WORKER topology, admission-gated at app startup (tortoise/worker_topology.py, #1677)"
    else
        echo "tortoise: uvicorn workers = 1"
    fi
fi

# ── #1677: the admission gate, BEFORE exec — so a refusal EXITS NON-ZERO ─────
# The block above closes the `--lifespan off` BYPASS. This one closes the
# ENFORCEMENT hole that remains when the lifespan DOES run: a refusal raised from
# a worker's startup exits **0**.
#
# MEASURED (uvicorn 0.52.1, a lifespan that raises): `--workers 3` -> exit 0;
# one worker -> exit 3. uvicorn's Multiprocess supervisor handles a worker's
# STARTUP_FAILURE by setting `should_exit` and RETURNING
# (`supervisors/multiprocess.py::keep_subprocess_alive`) — it never calls
# `sys.exit`; only the single-process path raises `SystemExit(3)`. This repo's
# `fly.toml` is `restart policy = "on-failure"` and records that a stopped
# machine is ZERO healthy instances (#2850). So the in-app refusal is loud and
# non-zero exactly when 1 worker runs, and silent-and-zero exactly when N>1
# runs — inert in the one case it exists for.
#
# Running the SAME policy pre-exec fixes that: it is the shipped image's path
# (`Dockerfile.hosted` ENTRYPOINT), the count is already measured above, and the
# refusal becomes `exit 1`, which `on-failure` sees. `refusal_reason` is the one
# policy — this is not a second copy of the rules — and the real uvicorn argv is
# passed through so `--reload` resolves exactly as the app will resolve it.
# The in-app gate stays as the backstop for a direct `uvicorn` invocation that
# bypasses this script.
if [ "$_IS_SERVER" = "1" ]; then
    # `|| _gate_rc=$?` is required, not style: this script runs `set -e`, so a
    # bare `_gate_out="$(…)"` would exit on the refusal BEFORE the diagnosis is
    # printed — leaving the operator with the policy text and no statement that
    # the GATE refused.
    _gate_rc=0
    _gate_out="$(python - "$@" <<'PY' 2>&1
import sys

from tortoise.worker_topology import observe, refusal_reason

reason = refusal_reason(observe(argv=sys.argv[1:]))
if reason:
    print(reason)
    raise SystemExit(1)
PY
    )" || _gate_rc=$?
    if [ "$_gate_rc" -ne 0 ]; then
        echo "tortoise: FATAL — #1677 multi-worker admission gate refused this boot (exit $_gate_rc):" >&2
        printf '%s\n' "$_gate_out" >&2
        exit 1
    fi
fi

exec "$@"
