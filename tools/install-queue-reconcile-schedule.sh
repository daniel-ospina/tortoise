#!/usr/bin/env bash
# install-queue-reconcile-schedule.sh — install the periodic CLAIMS.tsv
# reconciliation (issue #7810; the tool itself is #7800 / PR #7801).
#
# WHY THIS EXISTS (MEASURED, not inferred)
# ----------------------------------------
# `tools/queue_reconcile.py` reconciles the fleet's append-only PR queue
# (`~/.pi/agent/state/queues/CLAIMS.tsv`) against real GitHub state and appends
# a `LANDED` correction for every row whose recorded verdict contradicts a
# merge. It landed in #7801 and **nothing invoked it**: the first run at
# `origin/main` (d44505ccb) found 258 distinct identifiers and 64 applyable
# rows that had accumulated unnoticed over weeks. A reconciliation that only
# runs when a lane remembers to run it inherits the exact weakness #7800 was
# filed for ("nothing writes LANDED when a merge happens", row 7678).
#
# So this installs the missing trigger: `queue_reconcile.py --apply`, on a
# timer, with no lane in the loop.
#
# WHY THE ENTRY POINT IS THE CHECKOUT'S OWN PYTHON, NOT `bash`
# -----------------------------------------------------------
# macOS TCC is per-binary. A launchd-spawned `bash` is denied the read of a
# script under ~/Documents ("Operation not permitted"), and a launchd-spawned
# `git` the read of the repo there — the two failures recorded in
# templates/launchd/com.tortoise.worktree-reaper.plist and in agent-infra
# #427/#431. The binary that DOES hold the access is the checkout's own
# interpreter. `queue_reconcile.py` is stdlib-only and needs no `bash` leg at
# all, so the plist starts `<repo>/.venv/bin/python` directly on the tool and
# the `gh` it spawns inherits that access (the configuration the worktree
# reaper records as PROVEN end-to-end: `WORKTREES=194 … GH=ok`).
#
# WHY A SCHEDULE IS ENOUGH — THE TOOL'S SAFETY CONTRACT
# -----------------------------------------------------
# `--apply` is not a silent rewrite, so arming it unattended is safe:
#   * APPEND ONLY — one row, through the same `fcntl` lock `claim.py` uses;
#     the tool never rewrites or truncates the queue;
#   * TIMESTAMPED BACKUP — `CLAIMS.tsv.bak-reconcile-<stamp>` before the first
#     write of a run;
#   * IDEMPOTENT — `apply_corrections` RE-READS the queue and skips any number
#     already terminal, so a re-run cannot flag a corrected row again;
#   * ONLY PROOF-BACKED CORRECTIONS — the applyable set is
#     `{MERGED_WHILE_NOT_LANDED}` alone; a merged PR with its merge commit is
#     the proof the correct verdict is `LANDED`. Every kind that needs a human
#     read (BLOCKED_ON_CLOSED_ISSUE, CLOSED_ISSUE_WHILE_NON_TEMINAL, UNKNOWN,
#     MALFORMED) is REPORTED and never written.
#
# THE TOOL'S EXIT CODE IS A REPORT, NOT A JOB HEALTH SIGNAL
# ---------------------------------------------------------
# `0` clean / `1` contradiction(s) found / `2` UNKNOWN present / `3` queue
# missing. Under `--apply`, `1` and `2` are the NORMAL steady state while a
# report-only row exists in the queue (a closed issue that needs a human read,
# or a non-numeric probe identifier) — they mean "the run completed and has
# findings", not "the job failed". launchd does not alert on exit status; the
# READ ARTIFACT is the log. A resolver that is actually broken shows up there
# as a report in which nearly EVERY identifier is UNKNOWN, which is a different
# shape from the one or two standing report-only rows. Only `3` (queue gone)
# is unambiguously a broken install.
#
# USAGE
#   tools/install-queue-reconcile-schedule.sh            install (armed: --apply)
#   tools/install-queue-reconcile-schedule.sh --dry-run  install a REPORT-ONLY
#                                                        job (no --apply)
#   tools/install-queue-reconcile-schedule.sh --status   show install state
#   tools/install-queue-reconcile-schedule.sh --uninstall
#   tools/install-queue-reconcile-schedule.sh --help
#
# Env overrides:
#   TORTOISE_REPO        repo root (default: this script's repo)
#   PYTHON_BIN           interpreter for the run (default: <repo>/.venv/bin/python
#                        if present, else `command -v python3`). MUST be >= 3.12:
#                        tools/queue_reconcile.py refuses an older one, so this
#                        installer refuses to install a job that can only fail.
#   QUEUE_RECONCILE_INTERVAL  interval in SECONDS on BOTH platforms (launchd uses
#                        it directly; cron divides by 60), default 21600 (6h) —
#                        the same window the fleet's hub-state check uses.
#   CLAIMS_QUEUE         queue path (default $HOME/.pi/agent/state/queues/CLAIMS.tsv,
#                        the tool's own default). It is validated at install time
#                        and then passed to the job EXPLICITLY (`--queue <path>`):
#                        the install is refused if it does not exist, and the job
#                        can never drift onto a different, unchecked queue by
#                        falling back to an implicit HOME-derived default.
#   AGENTS_DIR           launchd install dir (default $HOME/Library/LaunchAgents).
#                        ⛔ A THROWAWAY $HOME/AGENTS_DIR DOES NOT SANDBOX THIS
#                        SCRIPT. `launchctl` addresses the user domain BY UID
#                        (`gui/$(id -u)`), so a bootstrap here re-points the LIVE
#                        agent launchd holds for that label whatever HOME says.
#                        The install is REFUSED unless AGENTS_DIR is the LOGIN
#                        ACCOUNT's <passwd-home>/Library/LaunchAgents (resolved
#                        from the password database, not the mutable $HOME) —
#                        unless QUEUE_RECONCILE_ALLOW_NONSTANDARD_AGENTS_DIR=1.
#   QUEUE_RECONCILE_ALLOW_NONSTANDARD_AGENTS_DIR  set to 1 to permit a
#                        non-standard AGENTS_DIR — at your own risk.
#   CRONTAB_CMD          crontab binary (default: crontab)
#
# Idempotent: a byte-identical rendered plist / cron line skips the reload.
# Safe to re-run on every sync.
#
# Exit codes: 0 = ok (or clean skip), 1 = failure (loud), 2 = usage/refusal.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${TORTOISE_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LABEL="com.tortoise.queue-reconcile"
PLIST_NAME="$LABEL.plist"
AGENTS_DIR="${AGENTS_DIR:-$HOME/Library/LaunchAgents}"
PLIST_PATH="$AGENTS_DIR/$PLIST_NAME"
CRONTAB_CMD="${CRONTAB_CMD:-crontab}"
CRON_MARKER="# tortoise-queue-reconcile (#7810)"
TOOL="$REPO/tools/queue_reconcile.py"
LOG_PATH="$HOME/.pi/agent/state/queue-reconcile.log"

if [ -x "$REPO/.venv/bin/python" ]; then
    PYTHON_BIN="${PYTHON_BIN:-$REPO/.venv/bin/python}"
else
    PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
fi

# ── fail-closed refusals: never install a job that can only fail ────────────
# (#5128 is the class: an unattributed failure reads as "this tool is broken".
# Here the equivalent is a scheduled job whose interpreter the tool REFUSES, or
# whose queue path does not exist — it would fail every fire, forever, and the
# only signal is a log nobody reads.)

if [ -z "${PYTHON_BIN:-}" ] || [ ! -x "$PYTHON_BIN" ]; then
    echo "ERROR: no usable interpreter (looked for $REPO/.venv/bin/python and python3)." >&2
    echo "       Queue_reconcile needs Python >= 3.12; set PYTHON_BIN to one." >&2
    exit 2
fi
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    echo "ERROR: refusing to install: $PYTHON_BIN is older than 3.12." >&2
    echo "       tools/queue_reconcile.py refuses a pre-3.12 interpreter, so the" >&2
    echo "       scheduled job would fail on every fire with no diagnostic gain." >&2
    echo "       Point PYTHON_BIN at a >= 3.12 interpreter (e.g. $REPO/.venv/bin/python)." >&2
    exit 2
fi

if [ ! -f "$TOOL" ]; then
    echo "ERROR: refusing to install: tool not found at $TOOL." >&2
    exit 2
fi

QUEUE_PATH="${CLAIMS_QUEUE:-$HOME/.pi/agent/state/queues/CLAIMS.tsv}"
if [ ! -f "$QUEUE_PATH" ]; then
    echo "ERROR: refusing to install: queue not found at $QUEUE_PATH." >&2
    echo "       A job pointed at a missing queue fails on every fire (the tool" >&2
    echo "       exits 3). Set CLAIMS_QUEUE, or install on the machine that holds it." >&2
    exit 2
fi

# `gh` must be resolvable at INSTALL time: launchd starts the job with a minimal
# PATH, so the directory holding gh is captured here and carried in the plist.
# Without it every identifier resolves to UNKNOWN — a report with no information.
GH_BIN="$(command -v gh || true)"
if [ -z "$GH_BIN" ]; then
    echo "ERROR: refusing to install: no 'gh' on PATH." >&2
    echo "       The tool resolves every identifier through the GitHub API; with no" >&2
    echo "       gh, every number is UNKNOWN and the report carries no information." >&2
    exit 2
fi

# The job's PATH: the directory holding the resolved gh, AHEAD of the ambient
# PATH. Carried into BOTH the plist and the cron line, because launchd's PATH is
# minimal and cron's default is `/usr/bin:/bin` — neither can find a gh installed
# under ~/.pi/agent/shims, ~/bin or /usr/local/bin. Without it every identifier
# resolves to UNKNOWN at fire time (the tool swallows the spawn failure into a
# per-number UNKNOWN), the report carries no information, and the install-time
# gh guard above would be giving false confidence about a job that can never
# work.
JOB_PATH="$(dirname "$GH_BIN")"
if [ "${PATH#"$JOB_PATH":}" != "$PATH" ]; then
    JOB_PATH="$PATH"          # gh's directory is already first — do not duplicate it
else
    JOB_PATH="$JOB_PATH:$PATH"
fi

# ── escaping ──────────────────────────────────────────────────────────────
# XML-escape a value before it is substituted into the plist template. Without
# this, a repo path containing `&` ("R&D") or `<` renders a plist that is NOT
# well-formed — yet the install still reports success, so the job is installed
# from a document launchd cannot parse. `&` MUST be replaced FIRST, or the `&`
# that the other replacements introduce would itself be escaped again.
_xml_escape() {
    local s="$1"
    s="${s//&/&amp;}"
    s="${s//</&lt;}"
    s="${s//>/&gt;}"
    printf '%s' "$s"
}

# crontab treats `%` as a command separator (everything after it becomes the
# command's stdin) and `\` as its escape, so a literal `%` in a path must be
# backslash-escaped or the rest of the schedule line is silently dropped.
_cron_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//%/\\%}"
    printf '%s' "$s"
}

# ── knobs (fail-closed numeric parsing) ────────────────────────────────────
# `[ "$X" -lt 1 ]` ERRORS on a value too large for the shell's integer type
# (status 2, which `if` reads as false), so a bare `-lt` guard is bypassed by a
# 20-digit value AND by an empty one. `! [ "$X" -ge 1 ]` turns both into the
# refusal branch; the digit bound rejects the overflow before any arithmetic.
# (Same shape and same reason as install-reaper-schedule.sh, #4438 review P2.)
_MAX_DIGITS=9
_require_positive_int() { # $1 = value, $2 = var name
    case "$1" in
        ''|*[!0-9]*)
            echo "ERROR: $2 must be a whole number, got '$1'" >&2
            return 2 ;;
    esac
    if [ "${#1}" -gt "$_MAX_DIGITS" ]; then
        echo "ERROR: $2 out of range (max ${_MAX_DIGITS} digits), got '$1'" >&2
        return 2
    fi
    if ! [ "$1" -ge 1 ] 2>/dev/null; then
        echo "ERROR: $2 must be >= 1, got '$1'" >&2
        return 2
    fi
    return 0
}

QUEUE_RECONCILE_INTERVAL="${QUEUE_RECONCILE_INTERVAL:-21600}"
_require_positive_int "$QUEUE_RECONCILE_INTERVAL" "QUEUE_RECONCILE_INTERVAL" || exit 2

# cron's minute step is only 0-59, so `*/N` with N >= 60 is evaluated over the
# minute range and matches minute 0 only — SILENTLY hourly, while launchd still
# gets N seconds. Render whole-hour intervals in the hour field; warn when cron
# cannot express the interval exactly. Computed once so the warning and the
# emitted field cannot drift.
CRON_MINUTES=$(( 10#$QUEUE_RECONCILE_INTERVAL / 60 ))
[ "$CRON_MINUTES" -lt 1 ] && CRON_MINUTES=1
if [ "$CRON_MINUTES" -lt 60 ]; then
    CRON_SCHEDULE="*/$CRON_MINUTES * * * *"
    if [ $(( 10#$QUEUE_RECONCILE_INTERVAL % 60 )) -ne 0 ]; then
        echo "WARNING: cron takes QUEUE_RECONCILE_INTERVAL in whole minutes; ${QUEUE_RECONCILE_INTERVAL}s floors to ${CRON_MINUTES} min" >&2
    fi
elif [ $(( CRON_MINUTES % 60 )) -eq 0 ] && [ $(( CRON_MINUTES / 60 )) -le 23 ]; then
    CRON_SCHEDULE="0 */$(( CRON_MINUTES / 60 )) * * *"
else
    CRON_SCHEDULE="0 * * * *"
    echo "WARNING: cron cannot express QUEUE_RECONCILE_INTERVAL ${QUEUE_RECONCILE_INTERVAL}s exactly (${CRON_MINUTES} min); scheduling hourly at minute 0" >&2
fi

usage() {
    sed -n '2,/^# Exit codes:/p' "$0" | sed 's/^# \{0,1\}//'
}

# ── the login account's home, from the password database ───────────────────
# The authority for "where the live agent lives" is the LOGIN ACCOUNT's home,
# NOT the exported $HOME, which a caller can point anywhere.
_login_home() {
    local user
    user="$(id -un)"
    if command -v getent >/dev/null 2>&1; then
        getent passwd "$user" 2>/dev/null | cut -d: -f6
    elif command -v dscl >/dev/null 2>&1; then
        dscl . -read "/Users/$user" NFSHomeDirectory 2>/dev/null \
            | awk '{print $2}'
    else
        eval echo "~$user"
    fi
}

require_standard_agents_dir() {
    local expected
    expected="$(_login_home)/Library/LaunchAgents"
    [ "$AGENTS_DIR" = "$expected" ] && return 0
    if [ "${QUEUE_RECONCILE_ALLOW_NONSTANDARD_AGENTS_DIR:-0}" = "1" ]; then
        echo "WARNING: AGENTS_DIR '$AGENTS_DIR' is not the login account's '$expected'; launchctl acts on gui/$(id -u)/$LABEL regardless (QUEUE_RECONCILE_ALLOW_NONSTANDARD_AGENTS_DIR=1)" >&2
        return 0
    fi
    echo "ERROR: refusing to install/uninstall to non-standard AGENTS_DIR '$AGENTS_DIR'." >&2
    echo "  launchctl addresses the user domain BY UID, so a throwaway HOME/AGENTS_DIR" >&2
    echo "  does NOT sandbox this install — it would re-point the LIVE" >&2
    echo "  gui/$(id -u)/$LABEL agent (login account dir: '$expected')." >&2
    echo "  Set QUEUE_RECONCILE_ALLOW_NONSTANDARD_AGENTS_DIR=1 to override." >&2
    return 2
}

# ── mode: armed (--apply) by default, report-only under --dry-run ──────────
# ARMED is the default because the whole point is to correct the queue with no
# lane in the loop; the mode is spelled out in the rendered schedule so a
# reviewer can grep what an installed job actually does rather than infer it
# from a default. In report-only mode NO mode argument is emitted, because
# tools/queue_reconcile.py has no `--dry-run` flag — dry run IS the absence of
# `--apply` — so passing one would make the tool die on an argparse error at
# every fire.
MODE_FLAG="--apply"

# ── rendering ─────────────────────────────────────────────────────────────
# The template is a QUOTED heredoc and every value is substituted with bash
# LITERAL parameter expansion (`${out//@@TOKEN@@/value}`).
#
# ⛔ WHY IT IS NOT AN UNQUOTED HEREDOC (measured 2026-10-08). An unquoted
# heredoc performs COMMAND SUBSTITUTION on any backtick in its body, and this
# body's prose necessarily names the `gh` binary — so rendering the plist
# EXECUTED `gh` (and `gh help accessibility`) and spliced their help output
# into an XML comment. Two harms, both silent:
#   1. an XML comment may not contain `--`, and gh's help is full of `--help`,
#      so the rendered plist is NOT well-formed — Python's expat/plistlib
#      refuse it (`not well-formed (invalid token)`) while `plutil -lint`,
#      which is lenient about comment bodies, still reports OK;
#   2. an install-time command runs that nobody asked for.
# Quoting the heredoc removes substitution entirely; literal expansion cannot
# execute anything, whatever a later editor writes in the prose.
_plist_template() {
    cat <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>@@LABEL@@</string>
  <!-- Entry point is the CHECKOUT'S OWN interpreter, not bash: macOS TCC is
       per-binary and denies a launchd-spawned bash the read of a script under
       ~/Documents, while this interpreter holds the access (see the header,
       and templates/launchd/com.tortoise.worktree-reaper.plist). -->
  <key>ProgramArguments</key>
  <array>
    <string>@@PYTHON_BIN@@</string>
    <string>@@TOOL@@</string>
@@MODE_ARG@@
    <!-- The queue is passed EXPLICITLY: the installer validates this exact path
         at install time, and an implicit HOME-derived default could silently
         point the job at a different queue than the one that was checked. -->
    <string>--queue</string>
    <string>@@QUEUE_PATH@@</string>
  </array>
  <key>WorkingDirectory</key><string>@@REPO@@</string>
  <key>EnvironmentVariables</key>
  <dict>
    <!-- HOME resolves ~ paths; PATH carries the gh directory, because
         launchd's own PATH is minimal. -->
    <key>HOME</key><string>@@HOME@@</string>
    <key>PATH</key><string>@@JOB_PATH@@</string>
  </dict>
  <key>StartInterval</key><integer>@@INTERVAL@@</integer>
  <key>StandardOutPath</key><string>@@LOG_PATH@@</string>
  <key>StandardErrorPath</key><string>@@LOG_PATH@@</string>
</dict>
</plist>
PLIST
}

render_plist() {
    # Every substituted VALUE is XML-escaped; the template's own markup and the
    # validated integer are not. Hoisted into locals rather than nested inside
    # the expansions below, so the escaping is visible per value.
    local mode_arg="" out
    local e_label e_python e_tool e_repo e_home e_jobpath e_qpath e_log
    [ -n "$MODE_FLAG" ] && mode_arg="    <string>$MODE_FLAG</string>"
    e_label="$(_xml_escape "$LABEL")"
    e_python="$(_xml_escape "$PYTHON_BIN")"
    e_tool="$(_xml_escape "$TOOL")"
    e_repo="$(_xml_escape "$REPO")"
    e_home="$(_xml_escape "$HOME")"
    e_jobpath="$(_xml_escape "$JOB_PATH")"
    e_qpath="$(_xml_escape "$QUEUE_PATH")"
    e_log="$(_xml_escape "$LOG_PATH")"
    out="$(_plist_template)"
    out="${out//@@LABEL@@/$e_label}"
    out="${out//@@PYTHON_BIN@@/$e_python}"
    out="${out//@@TOOL@@/$e_tool}"
    out="${out//@@MODE_ARG@@/$mode_arg}"
    out="${out//@@REPO@@/$e_repo}"
    out="${out//@@HOME@@/$e_home}"
    out="${out//@@JOB_PATH@@/$e_jobpath}"
    out="${out//@@QUEUE_PATH@@/$e_qpath}"
    out="${out//@@INTERVAL@@/$QUEUE_RECONCILE_INTERVAL}"
    out="${out//@@LOG_PATH@@/$e_log}"
    printf '%s\n' "$out"
}

cron_line() {
    local mode_arg=""
    [ -n "$MODE_FLAG" ] && mode_arg=" $MODE_FLAG"
    # `PATH=...` is the cron spelling of the plist's EnvironmentVariables: cron
    # runs the command through `/bin/sh -c`, so the assignment applies to THIS
    # command only. Same JOB_PATH as the plist, for the same reason.
    echo "$CRON_MARKER"
    echo "$CRON_SCHEDULE PATH=$(_cron_escape "$JOB_PATH") $(_cron_escape "$PYTHON_BIN") $(_cron_escape "$TOOL")${mode_arg} --queue $(_cron_escape "$QUEUE_PATH") >> $(_cron_escape "$LOG_PATH") 2>&1"
}

job_loaded() {
    launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1
}

install_darwin() {
    require_standard_agents_dir || return $?
    mkdir -p "$AGENTS_DIR"
    local tmp
    tmp="$(mktemp)" || return 1
    render_plist > "$tmp"
    local changed=1
    if [ -f "$PLIST_PATH" ] && cmp -s "$PLIST_PATH" "$tmp"; then
        changed=0
    fi
    mv "$tmp" "$PLIST_PATH"
    if [ "$changed" -eq 0 ]; then
        # Byte-identical plist does NOT imply a loaded agent: a manual bootout, a
        # failed load, or a reboot can leave the file present and the job
        # unloaded. Reporting "no reload" there would make the `--status`
        # remedy ("run this script with no arguments to load it") a no-op, so
        # the load is re-asserted whenever the label is absent.
        if job_loaded; then
            echo "unchanged: $PLIST_PATH (loaded; no reload)"
        else
            launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true
            if ! launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"; then
                echo "ERROR: launchctl bootstrap failed — see 'launchctl print gui/$(id -u)/$LABEL'" >&2
                return 1
            fi
            echo "unchanged: $PLIST_PATH (was NOT loaded — bootstrapped now)"
        fi
    else
        launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
        launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true
        if ! launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"; then
            echo "ERROR: launchctl bootstrap failed — see 'launchctl print gui/$(id -u)/$LABEL'" >&2
            return 1
        fi
        echo "installed + loaded: $PLIST_PATH (interval ${QUEUE_RECONCILE_INTERVAL}s, mode '${MODE_FLAG:-report-only}')"
    fi
    plutil -lint "$PLIST_PATH" >/dev/null 2>&1 \
        || { echo "ERROR: rendered plist is invalid" >&2; return 1; }
    return 0
}

install_linux() {
    local current new_line
    current="$($CRONTAB_CMD -l 2>/dev/null || true)"
    new_line="$(cron_line | tail -1)"
    if printf '%s\n' "$current" | grep -qF "$CRON_MARKER"; then
        # Replace any prior block (marker + schedule lines) by FIXED string, so
        # re-running neither accumulates markers nor strands a stale line.
        current="$(printf '%s\n' "$current" \
            | { grep -vF -e "$CRON_MARKER" -e "queue_reconcile.py" || true; })"
        if [ -n "$current" ]; then
            printf '%s\n%s\n%s\n' "$current" "$CRON_MARKER" "$new_line" | $CRONTAB_CMD - || return 1
        else
            printf '%s\n%s\n' "$CRON_MARKER" "$new_line" | $CRONTAB_CMD - || return 1
        fi
        echo "updated cron entry: $new_line"
    else
        if [ -n "$current" ]; then
            printf '%s\n%s\n%s\n' "$current" "$CRON_MARKER" "$new_line" | $CRONTAB_CMD - || return 1
        else
            printf '%s\n%s\n' "$CRON_MARKER" "$new_line" | $CRONTAB_CMD - || return 1
        fi
        echo "installed cron entry: $new_line"
    fi
    return 0
}

status() {
    echo "=== $LABEL ==="
    echo "repo       : $REPO"
    echo "tool       : $TOOL"
    echo "interpreter: $PYTHON_BIN ($("$PYTHON_BIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo '?'))"
    echo "queue      : $QUEUE_PATH"
    echo "interval   : ${QUEUE_RECONCILE_INTERVAL}s"
    echo "log        : $LOG_PATH"
    case "$(uname -s)" in
        Darwin)
            if [ -f "$PLIST_PATH" ]; then
                echo "installed  : $PLIST_PATH"
                if grep -qF -- "<string>--apply</string>" "$PLIST_PATH"; then
                    echo "mode       : ARMED (--apply)"
                else
                    echo "mode       : report-only (no --apply)"
                fi
                plutil -lint "$PLIST_PATH" >/dev/null 2>&1 && echo "plist lint : OK"
                local row
                row="$(launchctl list 2>/dev/null | grep -F "$LABEL" || true)"
                # job_loaded is the SAME predicate install_darwin acts on, so
                # `--status` and the install path cannot disagree about whether
                # the job is loaded (launchctl list is a host-domain view and can
                # miss a GUI-domain agent that `launchctl print` finds).
                if job_loaded; then
                    echo "loaded     : yes"
                    [ -n "$row" ] && echo "             launchctl list: $row"
                    echo "             (the exit code column is the tool's REPORT code: 0 clean,"
                    echo "              1 findings, 2 UNKNOWN present, 3 queue missing. 1/2 are the"
                    echo "              normal steady state while a report-only row stands — read"
                    echo "              $LOG_PATH for what the run actually measured.)"
                else
                    echo "loaded     : NO — run this script with no arguments to load it"
                fi
            else
                echo "installed  : NOT installed ($PLIST_PATH missing)"
            fi
            ;;
        Linux)
            if $CRONTAB_CMD -l 2>/dev/null | grep -qF "$CRON_MARKER"; then
                echo "installed  : cron entry present:"
                $CRONTAB_CMD -l 2>/dev/null | grep -F "$CRON_MARKER" -A1
            else
                echo "installed  : NOT installed (no $CRON_MARKER in crontab)"
            fi
            ;;
        *) echo "unsupported platform: $(uname -s)"; return 1 ;;
    esac
    return 0
}

uninstall() {
    case "$(uname -s)" in
        Darwin)
            require_standard_agents_dir || return $?
            launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
            rm -f "$PLIST_PATH"
            echo "removed $PLIST_PATH"
            ;;
        Linux)
            local current filtered
            current="$($CRONTAB_CMD -l 2>/dev/null || true)"
            # `grep -v` exits 1 when it selects NOTHING (a crontab holding only
            # our entries — exactly the state this installer creates). Capture
            # first, tolerating no-match, so the write is not reported failed.
            filtered="$(printf '%s\n' "$current" \
                | { grep -vF -e "$CRON_MARKER" -e "queue_reconcile.py" || true; })"
            if [ -n "$filtered" ]; then
                printf '%s\n' "$filtered" | $CRONTAB_CMD - || return 1
            else
                printf '' | $CRONTAB_CMD - || return 1
            fi
            echo "removed cron entry ($CRON_MARKER)"
            ;;
        *) echo "unsupported platform: $(uname -s)" >&2; return 1 ;;
    esac
    return 0
}

case "${1:-}" in
    --dry-run) MODE_FLAG=""; ;;
    --status) status; exit $? ;;
    --uninstall) uninstall; exit $? ;;
    --help|-h) usage; exit 0 ;;
    "") ;;
    *) echo "ERROR: unknown argument: $1 (use --dry-run, --status, --uninstall, or nothing)" >&2
       usage >&2; exit 2 ;;
esac

# NOTE: `--dry-run` is an INSTALLER flag (it selects the job's mode) and is
# consumed above — it is never passed through as an unknown argument.
case "$(uname -s)" in
    Darwin) install_darwin || exit $? ;;
    Linux) install_linux || exit $? ;;
    *) echo "ERROR: unsupported platform: $(uname -s) (launchd/cron only)" >&2; exit 1 ;;
esac
echo "queue-reconcile schedule installed (mode '${MODE_FLAG:-report-only}')."
echo "Verify: $0 --status"
echo "Logs:   $LOG_PATH"
