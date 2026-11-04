#!/usr/bin/env bash
# install-container-reaper-timer.sh — put the test-lane container sweep on a clock
# (the actuator half of #8180; the decision half is tools/test_lane.py).
#
# WHY A TIMER, AND WHY THIS ONE DID NOT EXIST
# -------------------------------------------
# `tools/test_lane.py::reap_orphans()` has decided correctly which `fdb-lane-*`
# containers are out of service since #5084/#8180/#10693/#10712 — three ownership
# proofs plus an age backstop. It had **no caller**: it is invoked from `start()`
# and from nothing else, so a lane that dies mid-turn (agent-infra #3391) leaves
# its container behind and **nothing on any box ever sweeps it**. The measured
# result: cell-1 **76** containers / load **285.53** on 8 cores, cell-4 **94**,
# cell-3 **19** — 189 stranded containers over three boxes, every one reaped **by
# hand**.
#
# The only scheduled sweep on a cell is `eldato-disk-reaper.timer` (15 min), whose
# classes are `uv / venv / worktree / duplicate-install`. A case-insensitive
# search for `docker` or `container` across that reaper and its delegates returns
# **0** — the container sink was never added to it. This unit is that missing
# clock. It calls `tools/container_reaper.py`, which owns no rule of its own and
# delegates every verdict to `reap_orphans()`.
#
# ⛔ DEPLOYED-VS-SOURCE: WHY THE HASH IS PINNED
# ---------------------------------------------
# The unit pins the **sha256 of the reaper it installed**
# (`Environment=TORTOISE_CONTAINER_REAPER_SHA256=…`). The reaper compares that
# pin against its own file and **refuses to sweep** on a mismatch (exit 4). So a
# unit still pointing at a moved, older, or hand-edited copy fails LOUDLY in
# `systemctl status` instead of sweeping with superseded policy — the failure
# mode the disk-reaper's own unit comments record ("a unit whose HOME happened to
# contain an older farmed copy silently ran THAT policy").
#
# The consequence is deliberate: **editing `tools/container_reaper.py` requires
# re-running this installer.** That is the drift check doing its job, not a bug.
#
# The unit is pinned to the **main checkout**, never to a lane worktree — a
# worktree is deleted when its lane lands, and a unit pointing into one would
# fail forever afterwards. It is also pinned to the **system** interpreter: both
# the reaper and `test_lane.py` are stdlib-only (no third-party import at module
# scope), so the sweep needs no venv and cannot be broken by one lane's `uv sync`.
#
# USAGE
#   tools/install-container-reaper-timer.sh                 install / update (idempotent)
#   tools/install-container-reaper-timer.sh --status        is it scheduled? what did it last do?
#   tools/install-container-reaper-timer.sh --uninstall     remove unit + timer
#   tools/install-container-reaper-timer.sh --dry-run       render the units, install nothing
#
# Env overrides:
#   REAPER_INTERVAL_SEC   cadence, default 900 (15 min — the same rhythm as the
#                         cell's disk reaper; see the rationale in the service unit)
#   REAPER_REPO           the checkout to pin (default: the MAIN worktree root)
#   PYTHON_BIN            interpreter (default: /usr/bin/python3 if >=3.12)
#   REAPER_ALLOW_WORKTREE_PIN=1  permit pinning a LANE worktree. Refused by
#                         default because a worktree is deleted when its lane
#                         lands and the unit would then fail forever. Set it ONLY
#                         to prove the mechanism end-to-end from a lane before the
#                         change merges, then re-run from main.
#
# Exit codes: 0 ok · 1 failure (loud) · 2 usage · 3 refused (unsafe target)
set -uo pipefail

LABEL="tortoise-container-reaper"
UNIT_DIR="/etc/systemd/system"
SERVICE="$UNIT_DIR/$LABEL.service"
TIMER="$UNIT_DIR/$LABEL.timer"
INTERVAL="${REAPER_INTERVAL_SEC:-900}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# `--help` is answered BEFORE the platform gate: asking a script what it does is
# not a request to install, and on macOS it exited 1 with "no systemd" instead.
# The header comments, up to the first executable line — a SENTINEL, not a line
# number: a hardcoded range printed `set -uo pipefail` as "help" the moment a
# header comment was added or removed (#10806 review P2).
help_text() {
    awk 'NR == 1 { next } /^set -/ { exit } /^#/ { sub(/^# ?/, ""); print }' \
        "${BASH_SOURCE[0]}"
}

case "${1:-}" in
    -h|--help) help_text; exit 0 ;;
esac

die()  { echo "ERROR: $*" >&2; exit 1; }
# A REFUSAL (an unsafe target the operator could name) is its own class, so a
# caller can tell "this box's reaper could not be installed" from "you asked for
# something this installer will never do".
refuse() { echo "REFUSED: $*" >&2; exit 3; }
usage() { help_text; exit "${1:-0}"; }

# ── platform gate ───────────────────────────────────────────────────────────
# Cells are Linux and every measured leak is on one. macOS has no systemd, and
# the repo already carries a launchd precedent for the OTHER (embedded) reaper —
# inventing a second macOS scheduler here without a measured leak to justify it
# would be machinery, not a fix. Refused, with the reason, rather than silently
# doing nothing.
if [ "$(uname -s)" != "Linux" ]; then
    die "$(uname -s) has no systemd. This installer arms the sweep on Linux cells,
        where the leak is measured (cell-1 76 containers / load 285.53). On macOS
        run the reaper by hand or from whatever scheduler that box already has:
          python3 $SELF_DIR/container_reaper.py --apply"
fi
command -v systemctl >/dev/null 2>&1 || die "systemctl not found — is this a systemd box?"

# ── the checkout to pin: the MAIN worktree, never a lane worktree ───────────
repo_root_main() {
    local root
    root="$(git -C "$SELF_DIR" worktree list --porcelain 2>/dev/null \
            | awk '/^worktree /{print substr($0,10); exit}')"
    [ -n "${root:-}" ] || root="$(git -C "$SELF_DIR" rev-parse --show-toplevel 2>/dev/null)"
    printf '%s' "$root"
}
REPO="${REAPER_REPO:-$(repo_root_main)}"
[ -n "${REPO:-}" ] || die "could not resolve the repo root (set REAPER_REPO)"
SOURCE="$REPO/tools/container_reaper.py"
POLICY="$REPO/tools/test_lane.py"
# `--status` asks what is DEPLOYED, so a missing source in whatever checkout the
# caller happens to stand in must be REPORTED, not fatal — that is precisely the
# drift this verb exists to show.
if [ "${1:-}" != "--status" ]; then
    [ -f "$SOURCE" ] || die "no reaper at $SOURCE"
fi

# Installing FROM a lane worktree is fine; PINNING to it is not — that guard is
# the `$REPO` case immediately below.
case "$REPO" in
    */.worktrees/*)
        if [ "${REAPER_ALLOW_WORKTREE_PIN:-0}" != "1" ]; then
            refuse "refusing to pin the unit to a LANE worktree ($REPO).
        A worktree is deleted when its lane lands, so the unit would fail forever
        after. Pin the main checkout instead: REAPER_REPO=/path/to/main $0
        (Set REAPER_ALLOW_WORKTREE_PIN=1 only to prove the mechanism from a lane
        before the change merges.)"
        fi
        echo "WARNING: pinning a LANE worktree ($REPO)."
        echo "         This unit WILL fail once that lane lands. Re-run this"
        echo "         installer from the main checkout to re-point it."
        echo ;;
esac

# ── the interpreter: stdlib only, so no venv ────────────────────────────────
if [ -n "${PYTHON_BIN:-}" ]; then
    PY="$PYTHON_BIN"
elif [ -x /usr/bin/python3 ]; then
    PY=/usr/bin/python3
else
    PY="$(command -v python3 || true)"
fi
[ -n "${PY:-}" ] || die "no python3 found (set PYTHON_BIN)"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
    || die "$PY is older than 3.12, which the reaper refuses at import time"

# The pins are only needed to INSTALL. `--status` reports what is deployed, so it
# must run from anywhere (a lane worktree, a fresh clone with no main checkout).
if [ "${1:-}" != "--status" ]; then
command -v sha256sum >/dev/null 2>&1 \
    || die "sha256sum not found — the deployed-vs-source pin cannot be computed,
        and an EMPTY pin silently disables the reaper's staleness check"
# ⛔ TWO pins. `container_reaper.py` only enumerates and reports; every verdict
# comes from `tools/test_lane.py::reap_orphans()`, which is the file that decides
# what gets DELETED. Pinning only the safe half would invert the risk.
[ -f "$POLICY" ] || die "no decision half at $POLICY"
SHA="$(sha256sum "$SOURCE" | cut -d' ' -f1)"
POLICY_SHA="$(sha256sum "$POLICY" | cut -d' ' -f1)"
[ -n "$SHA" ] && [ -n "$POLICY_SHA" ] \
    || die "could not hash the reaper or its policy file — refusing to install a
        unit whose pin would be empty (an empty pin disables the check)"
fi
# `REAPER_INTERVAL_SEC` -> an `OnCalendar` slot spec. Only whole-minute cadences
# have a calendar form; anything else is refused rather than silently rounded.
CALENDAR_FOR() {
    local secs="$1" mins
    case "$secs" in (*[!0-9]*) return 1;; esac
    if [ "$secs" -eq 3600 ]; then echo "hourly"; return 0; fi
    if [ $((secs % 60)) -ne 0 ]; then return 1; fi
    mins=$((secs / 60))
    if [ "$mins" -lt 1 ] || [ "$mins" -gt 60 ] || [ $((60 % mins)) -ne 0 ]; then
        return 1
    fi
    echo "*:0/$mins"
}
CALENDAR="$(CALENDAR_FOR "$INTERVAL")" || die "REAPER_INTERVAL_SEC=$INTERVAL has no \
        OnCalendar form (use a whole-minute divisor of 60: 60…3600). Refused rather \
        than silently rounded to a cadence nobody chose."

render_service() {
    cat <<EOF
[Unit]
# tortoise container reaper — the actor that reaps test-lane FalkorDB containers
# which are PROVABLY out of service (#8180). It owns no ownership rule: every
# verdict comes from tools/test_lane.py::reap_orphans(), the same call a lane's
# own \`start()\` makes, so the clock and the lane can never disagree.
#
# WHY 15 MINUTES (REAPER_INTERVAL_SEC). Containers leak at lane CHURN, not at a
# steady rate — measured on this cell: 45 lane-worktree starts in 24 h (~1.9/h),
# each leaving one container when the lane never reaches \`down\`. A 15-minute
# tick bounds the strand to under one container between sweeps, matches the
# cadence the cell's own disk reaper already runs at (one sweep rhythm, not two),
# and costs two \`docker ps\` calls when there is nothing to do. The primary fix is
# tools/test_lane.py's exit hook, which removes the container when a lane ENDS;
# this timer is the backstop for the lanes that are KILLED (agent-infra #3391) and
# therefore never reach it.
Description=tortoise container reaper — reap test-lane FalkorDB containers that are provably out of service
After=docker.service local-fs.target
# A reaper must never hold up a boot, and must never be a reason to keep a box up.
DefaultDependencies=no

[Service]
Type=oneshot
User=${SUDO_USER:-$(id -un)}
# ⛔ DEPLOYED-VS-SOURCE. The pin below is the sha256 of the file this installer
# read. The reaper re-hashes ITSELF and refuses to sweep on a mismatch (exit 4),
# so this unit cannot quietly run a stale or hand-edited copy — re-run the
# installer after any edit. Installer: tools/install-container-reaper-timer.sh
Environment=TORTOISE_CONTAINER_REAPER_SHA256=$SHA
Environment=TORTOISE_TEST_LANE_POLICY_SHA256=$POLICY_SHA
# ⛔ `WorkingDirectory=` takes NO quoting — systemd reads the quotes as part of the
# path ("path is not absolute: \"/home/…\"") and the unit is a fatal error.
# `ExecStart=` DOES, and a checkout path with a space needs it there.
WorkingDirectory=$REPO
ExecStart="$PY" "$SOURCE" --apply --json
# Never compete with a lane for CPU: this is a background sweep, not a deadline.
Nice=10
TimeoutStartSec=300
# Exit 5 = a removal FAILED (a container that needed to go is still up),
# 4 = stale copy, 2 = docker unreadable / the sweep could not decide, 3 = usage.
# ALL of them are REPORTED by the unit FAILING; none is swallowed into green.
EOF
}

render_timer() {
    cat <<EOF
[Unit]
Description=tortoise container reaper timer (every ${INTERVAL}s)

[Timer]
# OnCalendar, not OnUnitActiveSec: \`Persistent=\` is only honoured by CALENDAR
# timers, and the property it buys is the one that matters here — a cell that was
# suspended or rebooted runs its sweep as soon as it comes back, instead of
# waiting out a whole interval while containers pile up. It also gives the timer
# a NEXT elapse before the service has ever run, which a monotonic timer does not.
# The slot maths is derived from the same REAPER_INTERVAL_SEC, so the cadence has
# one source of truth: 900s -> \`*:0/15\`.
OnCalendar=${CALENDAR}
AccuracySec=60
RandomizedDelaySec=60
Persistent=true
Unit=$LABEL.service

[Install]
WantedBy=timers.target
EOF
}

install_all() {
    render_service | sudo tee "$SERVICE" >/dev/null || die "could not write $SERVICE"
    render_timer   | sudo tee "$TIMER"   >/dev/null || die "could not write $TIMER"
    sudo systemctl daemon-reload || die "daemon-reload failed"
    sudo systemctl enable --now "$LABEL.timer" || die "could not enable $LABEL.timer"
    echo "installed: $SERVICE"
    echo "installed: $TIMER"
    echo "pinned sha256: $SHA  (tools/container_reaper.py)"
    echo "pinned sha256: $POLICY_SHA  (tools/test_lane.py — the decision half)"
    echo "pinned checkout: $REPO"
    echo "cadence: ${INTERVAL}s"
    echo
    echo "scheduler state:"
    sudo systemctl list-timers "$LABEL.timer" --all --no-pager || true
}

status_all() {
    echo "── unit files ──"
    for f in "$SERVICE" "$TIMER"; do
        if [ -f "$f" ]; then echo "present  $f"; else echo "MISSING  $f"; fi
    done
    echo
    echo "── the scheduler's own status verb ──"
    systemctl list-timers "$LABEL.timer" --all --no-pager 2>&1 || true
    echo
    systemctl is-enabled "$LABEL.timer" 2>&1 | sed 's/^/enabled: /'
    systemctl is-active  "$LABEL.timer" 2>&1 | sed 's/^/active:  /'
    echo
    echo "── deployed-vs-source ──"
    # Read from the INSTALLED unit. Re-resolving the repo here would answer a
    # different question ("what would an install do now") and would fail outright
    # when run from a lane worktree — the check has to describe the deployment.
    local pinned policy_pin exec_line deployed live src_live policy_live
    pinned="$(sed -n 's/^Environment=TORTOISE_CONTAINER_REAPER_SHA256=//p' \
              "$SERVICE" 2>/dev/null)"
    policy_pin="$(sed -n 's/^Environment=TORTOISE_TEST_LANE_POLICY_SHA256=//p' \
              "$SERVICE" 2>/dev/null)"
    exec_line="$(sed -n 's/^ExecStart=//p' "$SERVICE" 2>/dev/null)"
    # The script is the one token ending in `.py`; taking `$NF` would pick the
    # LAST flag (`--json`) and report the flags as the deployment (measured).
    # `ExecStart` quotes its argv (a path with a space), so the quote characters
    # must come off before the `.py` token can be recognised — measured: without
    # this the report said "deployed at: <none>" for a healthy unit.
    deployed="$(echo "$exec_line" | tr ' ' '\n' | tr -d '"' \
                | grep -E '\.py$' | head -1)"
    live="$([ -n "${deployed:-}" ] && sha256sum "$deployed" 2>/dev/null | cut -d' ' -f1)"
    src_live="$([ -f "$SOURCE" ] && sha256sum "$SOURCE" 2>/dev/null | cut -d' ' -f1)"
    # The POLICY the DEPLOYED reaper will import: the deployed checkout's own
    # tools/test_lane.py, not this caller's checkout.
    policy_live=""
    # Only when the deployment was actually parsed — otherwise `dirname ""` is
    # `.` and this hashes the CALLER's checkout, printing a verdict about a file
    # the unit does not run.
    if [ -n "${deployed:-}" ]; then
        policy_live="$(sha256sum "$(dirname "$deployed")/test_lane.py" \
            2>/dev/null | cut -d' ' -f1)"
    fi
    echo "deployed at  : ${deployed:-<none>}"
    echo "deployed pin : ${pinned:-<none>}  (actuator)"
    echo "policy pin   : ${policy_pin:-<none>}  (tools/test_lane.py)"
    echo "policy sha   : ${policy_live:-<unreadable>}"
    echo "deployed sha : ${live:-<unreadable>}"
    echo "source here  : $SOURCE"
    echo "source sha   : ${src_live:-<missing>}"
    if [ -z "${pinned:-}" ] || [ -z "${policy_pin:-}" ]; then
        echo "verdict      : UNPINNED — the unit carries no pin (or no policy"
        echo "               pin), so part of the staleness check is inert."
        echo "               Re-run this installer."
    elif [ "$policy_pin" != "${policy_live:-}" ]; then
        echo "verdict      : POLICY DRIFT — tools/test_lane.py (the decision"
        echo "               half) no longer matches its pin; the unit refuses"
        echo "               to sweep (exit 4). Re-run this installer."
    elif [ "$pinned" != "${live:-}" ]; then
        echo "verdict      : STALE COPY — the unit would refuse to sweep (exit 4)."
        echo "               Re-run this installer."
    elif [ -n "${src_live:-}" ] && [ "$pinned" != "$src_live" ]; then
        echo "verdict      : IN SYNC WITH ITS PIN, but the source in THIS checkout"
        echo "               has moved on — the deployment is behind. Re-run this"
        echo "               installer to re-pin and re-deploy."
    else
        echo "verdict      : IN SYNC — the installed unit runs this checkout's source."
    fi
    case "${deployed:-}" in
        */.worktrees/*)
            echo "⚠️  the deployment is pinned to a LANE WORKTREE — it will fail"
            echo "    when that lane lands. Re-run the installer from main." ;;
    esac
    echo
    echo "── last run ──"
    systemctl status "$LABEL.service" --no-pager -l 2>&1 | sed -n '1,14p' || true
    echo
    journalctl -u "$LABEL.service" -n 20 --no-pager 2>&1 | tail -20 || true
}

uninstall_all() {
    # ⛔ Never report a removal that did not happen. The old form swallowed both
    # failures with `|| true` and then printed "removed" — the exact class this
    # tool refuses everywhere else (#10813 review P3).
    local rc=0
    sudo systemctl disable --now "$LABEL.timer" 2>/dev/null || true
    sudo rm -f "$TIMER" "$SERVICE" || rc=1
    sudo systemctl daemon-reload || rc=1
    if [ -f "$TIMER" ] || [ -f "$SERVICE" ]; then rc=1; fi
    if [ "$rc" -ne 0 ]; then
        echo "ERROR: uninstall INCOMPLETE — the unit file(s) or the timer state" >&2
        echo "       could not be removed. Check: systemctl status $LABEL.timer" >&2
        exit 1
    fi
    echo "removed $TIMER and $SERVICE (verified absent)"
}

case "${1:-}" in
    ""|--install)  install_all ;;
    --status)      status_all ;;
    --uninstall)   uninstall_all ;;
    --dry-run)
        echo "### $SERVICE"; render_service
        echo; echo "### $TIMER"; render_timer ;;
    -h|--help)     usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 2 ;;
esac
