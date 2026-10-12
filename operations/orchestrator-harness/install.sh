#!/usr/bin/env bash
# install.sh — render + install the launchd agent for the orchestrator harness.
#
# Deliberately does NOT start the loop. It writes the plist and prints the load command.
# Starting an autonomous merge loop is an explicit act, not a side effect of installing.
#
#   ./install.sh                 # render + install (idempotent)
#   ./install.sh --uninstall     # unload + remove the plist (leaves state/logs alone)
#
# Config comes from the environment; defaults are the values this harness was built with
# (see README §4). Nothing is written outside ~/Library/LaunchAgents and $O.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

O="${O:-$HOME/orchestrator}"
REPO="${REPO:-/Users/home/eldato/tortoise}"
OR="${OR:-daniel-ospina/tortoise}"

MAX_LOAD="${MAX_LOAD:-16}"
MAX_LANES="${MAX_LANES:-4}"
MAX_REVIEW="${MAX_REVIEW:-4}"
MERGE_PRIORITY_AT="${MERGE_PRIORITY_AT:-10}"
MAX_FIX="${MAX_FIX:-3}"
MAX_FIX_TRIES="${MAX_FIX_TRIES:-3}"
MIN_SWAP_FREE="${MIN_SWAP_FREE:-600}"
BATCH="${BATCH:-2}"
CYC="${CYC:-240}"

LABEL="com.eldato.orchestrator-loop"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TEMPLATE="$HERE/launchd.plist.template"

die() { echo "❌ $*" >&2; exit 1; }
ok()  { echo "  ✓ $*"; }
warn(){ echo "  ⚠️  $*"; }

# ── uninstall ────────────────────────────────────────────────────────────────────
if [ "${1:-}" = "--uninstall" ]; then
  launchctl unload -w "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "✅ Removed $LABEL."
  echo "   State and logs left intact: $O/{logs,state,wip}"
  exit 0
fi

# ── platform ─────────────────────────────────────────────────────────────────────
echo "==> preflight"
[ "$(uname)" = "Darwin" ] || die "this harness uses launchd + tmux; only macOS is supported"
[ -f "$TEMPLATE" ] || die "missing template: $TEMPLATE"

# ── dependencies ─────────────────────────────────────────────────────────────────
for bin in tmux gh pi; do
  command -v "$bin" >/dev/null 2>&1 && ok "$bin" || die "$bin not on PATH"
done
command -v uv >/dev/null 2>&1 && ok "uv" || warn "uv not found — lane.sh needs it to sync each worktree"
gh auth status >/dev/null 2>&1 && ok "gh authenticated" || die "gh is not authenticated"

# ── the daemon's PATH ────────────────────────────────────────────────────────────
# ⛔ launchd gives a process NO shell environment, so every binary the daemon shells out to must be
# findable on the PATH baked into the plist. The original version hardcoded three brew dirs, which
# was correct only because gh/uv/pi happened to be brew-installed on the box it was built on.
# On a box where they live under the user's own prefix the loop does NOT fail loudly — it reads an
# EMPTY QUEUE: `OPEN_N="$(gh pr list ... 2>/dev/null)"` yields "" and `${OPEN_N:=0}` turns that
# into 0, so the sweep logs `[scope] open=0 -> OWN` while the queue is full and the loop idles.
# Measured 2026-10-12 on a second box: 25 open PRs invisible; `pi` missing would also have broken
# every lane spawn and `uv` every worktree sync. Resolve the real directories instead of assuming.
# Three traps here, and ALL fail silently at runtime rather than loudly at install time:
#  (1) `command -v` can return a WRAPPER, not the tool. Where `gh` is the pi agent shim
#      (~/.pi/agent/shims/gh), the resolved directory is the SHIM's — and the shim locates the real
#      gh by scanning PATH minus its own directory, finds nothing, and exits 127. The loop then
#      reads an empty queue and logs `[scope] open=0 -> OWN`. A shimmed tool therefore needs the
#      directory of its REAL binary on the PATH too.
#  (2) `command -v` can return a NON-ABSOLUTE name (a shell function/alias), and `dirname` of that
#      is `.` — baking the current directory into an unattended daemon's PATH.
#  (3) ⛔ A shim directory must NEVER be baked into the daemon PATH at all — under launchd the shim
#      can be UNEXECUTABLE. ~/.pi/agent/shims/gh is a symlink INTO ~/Documents, and macOS TCC
#      (Transparency, Consent & Control) blocks launchd from reading ~/Documents, so the daemon
#      gets `bash: ~/.pi/agent/shims/gh: Operation not permitted` — EPERM (a TCC denial on the
#      symlink's TARGET), NOT a missing file or permission bit. The install shell is interactive
#      and CAN read ~/Documents, so the probe-by-execution below SUCCEEDS and install reports OK:
#      a FALSE GREEN for exactly the silent failure this block exists to prevent (the daemon later
#      reads an empty queue and idles at open=0). No install-time probe can see the EPERM, so the
#      exclusion below — not a stronger probe — is the guard. add_dir() therefore refuses every
#      `*/shims`, `*/shims/*` and `*/.pi/*` directory while the "resolve PAST a wrapper" loop keeps
#      adding the REAL binary's directory, so the tool is still findable. Do not "simplify" this
#      exclusion away: it is invisible to every check that runs before launchd does.
add_dir() {
  case "$1" in /*) ;; *) return 0 ;; esac
  # ⛔ Trap (3): a shim / ~/.pi directory is EPERM under launchd (macOS TCC on ~/Documents). The
  # real binary's directory is added separately, so dropping the shim does not lose the tool.
  # Test the PHYSICAL path (pwd -P), not the name: a PATH entry that is a SYMLINK to the shim
  # directory matches none of the name patterns, and without this it slips through AND suppresses
  # the real binary (the resolve-past loop keys off the same patterns). Store the LOGICAL path so
  # the version-independent pi symlink is not pinned to a version-stamped target.
  local _phys
  _phys="$(cd "$1" 2>/dev/null && pwd -P)" || return 0
  case "$_phys" in ""|*/shims|*/shims/*|*/.pi/*) return 0 ;; esac
  case ":$DAEMON_PATH:" in *":$1:"*) ;; *) DAEMON_PATH="$DAEMON_PATH:$1" ;; esac
}
DAEMON_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# Prefer the version-independent pi symlink: a resolved version-stamped pi-node directory is pinned
# to today's version, and a pi upgrade would leave the daemon on a stale (or missing) one.
if [ -x "$HOME/.local/share/pi-node/current/bin/pi" ]; then
  command -v pi >/dev/null 2>&1 && add_dir "$HOME/.local/share/pi-node/current/bin"
fi
for bin in tmux gh uv pi; do
  # ⛔ Resolve the BINARY, not a shell function/alias. `command -v` reports an exported function
  # REGARDLESS of PATH (and a BARE name for it), so with a `gh`/`pi` function in the environment the
  # real binary's directory would never be found AND the name would be unusable. `type -P` searches
  # PATH ONLY (ignoring functions/aliases) and returns an absolute path — exactly what launchd will
  # exec — so trap (2) (a `.`-dirname from a bare name) cannot arise; the `/*` guard stays as
  # defence in depth.
  p="$(type -P "$bin" 2>/dev/null)" || continue
  case "$p" in /*) ;; *) continue ;; esac
  add_dir "$(cd "$(dirname "$p")" 2>/dev/null && pwd)"
done
# Resolve PAST a wrapper: add the first PATH entry holding a real executable of that name outside a
# shim/.pi directory, so the shim can find it. Tested on the PHYSICAL path like add_dir(), so a
# symlink TO a shim directory is recognised as a shim too.
for bin in gh pi uv tmux; do
  p="$(type -P "$bin" 2>/dev/null)" || continue
  case "$p" in /*) ;; *) continue ;; esac
  _pd="$(cd "$(dirname "$p")" 2>/dev/null && pwd -P)" || continue
  case "$_pd" in
    */shims|*/shims/*|*/.pi/*)
      _oldifs="$IFS"; IFS=:
      # `for d in $PATH` word-splits on `:` and then GLOBS each entry: an entry containing `*`
      # would expand to directories that are not PATH entries. Disable globbing for the scan.
      case $- in *f*) _had_noglob=1 ;; *) _had_noglob=0 ;; esac
      set -f
      for d in $PATH; do
        [ -n "$d" ] || continue
        _dd="$(cd "$d" 2>/dev/null && pwd -P)" || continue
        case "$_dd" in ""|*/shims|*/shims/*|*/.pi/*) continue ;; esac
        if [ -x "$d/$bin" ]; then add_dir "$d"; break; fi
      done
      [ "$_had_noglob" = 1 ] || set +f
      IFS="$_oldifs" ;;
  esac
done
ok "daemon PATH: $DAEMON_PATH"
# ⛔ PROBE BY EXECUTION AND BY ABSOLUTE PATH. Resolving a name is not running it, and `command -v`
# cannot answer the daemon's question: it reports an EXPORTED SHELL FUNCTION (which launchd never
# sees), and executed by bare name it would run the function instead of the daemon's binary — a
# false green. `type -P` under the daemon PATH returns the absolute binary launchd will exec
# (ignoring functions/aliases); each is then EXECUTED. A shim/wrapper directory that does not match
# the exclusion patterns (cmux's `cmux-cli-shims` is a real one) is caught here because its tool
# exits 127. uv is optional (warned above): its failure WARNS, and is never fatal. Still BLIND to
# trap (3) — TCC refuses the read only when LAUNCHD does it, not when an interactive shell does.
for bin in tmux uv pi gh; do
  command -v "$bin" >/dev/null 2>&1 || continue     # not present in this shell at all
  # ⛔ `|| true`: install.sh runs under `set -euo pipefail`, and `type -P` returns non-zero for a
  # tool that is not on $DAEMON_PATH. Unguarded, that bare assignment aborts the script AT THIS
  # LINE — before the `if [ -n "$_p" ]` test below — so the operator gets a bare rc=1 with no
  # diagnostic, and the optional `uv` (documented and warned as non-fatal above) becomes fatal.
  _p="$(PATH="$DAEMON_PATH" type -P "$bin" 2>/dev/null || true)"
  case "$_p" in /*) ;; *) _p="" ;; esac
  # tmux takes -V; the rest take --version. Either way it must actually EXECUTE.
  _vflag="--version"; [ "$bin" = "tmux" ] && _vflag="-V"
  # ⛔ Execute under the DAEMON PATH, not this shell's: a wrapper that DELEGATES by name (strip
  # its own dir, `exec $bin`) would find the tool on the interactive PATH and exit 0 while launchd
  # gets 127 — the same false green one layer in. The prefix reproduces the daemon's environment.
  if [ -n "$_p" ] && PATH="$DAEMON_PATH" "$_p" "$_vflag" >/dev/null 2>&1; then continue; fi
  if [ "$bin" = "uv" ]; then
    warn "daemon PATH cannot run uv — lane.sh will not sync worktrees: $DAEMON_PATH"
  else
    die "daemon PATH cannot run $bin: $DAEMON_PATH"
  fi
done

# ── the merge authority ──────────────────────────────────────────────────────────
# The harness can review and record, but it cannot MERGE without the gate key.
# IMPORTANT: only the key FILE reaches the daemon. launchd inherits no shell environment, and
# install.sh deliberately does not copy a secret into a world-readable plist — so an env-only key
# must be reported as insufficient rather than as "present", or the box silently degrades to
# review-only while everyone believes it holds the merge authority.
KEY_FILE="$HOME/.pi/agent/.ai-review-gate-key"
if [ -f "$KEY_FILE" ]; then
  ok "gate key file present ($KEY_FILE)"
  [ -n "${AI_REVIEW_GATE_KEY:-}" ] && ok "AI_REVIEW_GATE_KEY also exported in this shell"
elif [ -n "${AI_REVIEW_GATE_KEY:-}" ]; then
  warn "AI_REVIEW_GATE_KEY is set in THIS SHELL ONLY — launchd will not see it."
  warn "  → the daemon will record reviews UNSIGNED and ai-review-gate will fail: no merge role."
  warn "  → fix: printf '%s' \"\$AI_REVIEW_GATE_KEY\" > $KEY_FILE && chmod 600 $KEY_FILE"
else
  warn "no AI_REVIEW_GATE_KEY (neither $KEY_FILE nor the environment)"
  warn "  → the loop degrades to review/record only. Fine for evaluation; not for a merge role."
fi
[ -f "$HOME/.pi/agent/scripts/record-review.sh" ] \
  && ok "record-review.sh" \
  || warn "~/.pi/agent/scripts/record-review.sh missing — record step will fail (shipped by agent-infra)"

# ── target repo ──────────────────────────────────────────────────────────────────
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || die "not a git checkout: $REPO"
git -C "$REPO" check-ignore -q .worktrees/ \
  && ok "$REPO/.worktrees is gitignored" \
  || die "$REPO/.worktrees is NOT gitignored — worktrees would be committed. Fix .gitignore first."
[ -f "$REPO/tools/collision_preflight.py" ] \
  && ok "collision pre-flight present" \
  || warn "tools/collision_preflight.py missing — refresh-candidates.sh will add nothing to the backlog"
if [ ! -x "$REPO/.worktrees/dispatch-scout/.venv/bin/python" ]; then
  warn "scout worktree/venv not provisioned at $REPO/.worktrees/dispatch-scout"
  warn "  → refresh-candidates.sh will provision it on first run (git worktree add + uv sync),"
  warn "    which takes a couple of minutes and needs network access."
fi

# ── shipped scripts ──────────────────────────────────────────────────────────────
for f in "$HERE"/*.sh; do
  bash -n "$f" || die "syntax error in $(basename "$f")"
done
ok "all shipped scripts parse"

# ── state dir ────────────────────────────────────────────────────────────────────
mkdir -p "$O/logs" "$O/state" "$O/wip"
touch "$O/candidates.txt" "$O/dispatched.txt" "$O/checked.txt"
ok "state dir $O"

# ── render plist ─────────────────────────────────────────────────────────────────
echo "==> rendering $PLIST"
# Two independent levels of escaping are required, and they fail differently:
#  1. XML — a bare & < > makes the plist invalid XML and launchd will not load it. (plutil -lint
#     catches this, so it fails loudly rather than silently.)
#  2. sed — an unescaped & in the REPLACEMENT expands to the matched text, so `O=/tmp/a&b`
#     rendered the path as /tmp/a__O__b: a syntactically VALID plist pointing at wrong paths,
#     which plutil -lint cannot catch. Escape \, & and the | delimiter too.
xml_esc() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'; }
sed_esc() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }
esc() { sed_esc "$(xml_esc "$1")"; }
sed \
  -e "s|__O__|$(esc "$O")|g" \
  -e "s|__REPO__|$(esc "$REPO")|g" \
  -e "s|__OR__|$(esc "$OR")|g" \
  -e "s|__HOME__|$(esc "$HOME")|g" \
  -e "s|__PATH__|$(esc "$DAEMON_PATH")|g" \
  -e "s|__MAX_LOAD__|$MAX_LOAD|g" \
  -e "s|__MAX_LANES__|$MAX_LANES|g" \
  -e "s|__MAX_REVIEW__|$MAX_REVIEW|g" \
  -e "s|__MERGE_PRIORITY_AT__|$MERGE_PRIORITY_AT|g" \
  -e "s|__MAX_FIX__|$MAX_FIX|g" \
  -e "s|__MAX_FIX_TRIES__|$MAX_FIX_TRIES|g" \
  -e "s|__MIN_SWAP_FREE__|$MIN_SWAP_FREE|g" \
  -e "s|__BATCH__|$BATCH|g" \
  -e "s|__CYC__|$CYC|g" \
  "$TEMPLATE" > "$PLIST"
plutil -lint "$PLIST" >/dev/null || die "rendered plist is invalid"
ok "plist valid"

# launchd runs $O/<script>, not the copies in this directory — check EVERY script the daemon
# invokes, not just orch-loop.sh. A drift here is silent and serious: a fix applied only to the
# shipped copy (e.g. the fail-closed ai-review-gate classifier) would never reach the running box.
DRIFTED=""
for f in orch-loop.sh dispatch-merge.sh lane.sh chain-merge.sh merge-one.sh fix-one.sh refresh-candidates.sh adopt-fix.sh; do
  [ -f "$O/$f" ] || { DRIFTED="$DRIFTED $f(missing)"; continue; }
  diff -q "$HERE/$f" "$O/$f" >/dev/null 2>&1 || DRIFTED="$DRIFTED $f"
done
if [ -n "$DRIFTED" ]; then
  warn "the running copy at \$O differs from the shipped copy:$DRIFTED"
  warn "  the daemon executes \$O/<script> — sync them or the fixes here are not in effect:"
  warn "  cp '$HERE'/*.sh '$O'/";
fi

cat <<EOF

✅ Installed $LABEL

  harness   $O
  repo      $REPO   ($OR)
  limits    lanes=$MAX_LANES review=$MAX_REVIEW fix=$MAX_FIX fix_tries=$MAX_FIX_TRIES
            max_load=$MAX_LOAD min_swap_free=${MIN_SWAP_FREE}M batch=$BATCH cycle=${CYC}s
            merge_priority_at=${MERGE_PRIORITY_AT} open PRs

The loop is NOT running. To start it:

    launchctl load -w "$PLIST"

Watch it (the first line of the decision log each cycle):

    tail -f "$O/logs/orch-loop.log"

Liveness — a stale heartbeat means the controller is wedged:

    echo \$(( \$(date +%s) - \$(cat "$O/heartbeat") ))   # seconds since last cycle

Stop it before handing the machine to a human, and capture dirty work (README §6):

    launchctl unload -w "$PLIST"; pkill -f orch-loop.sh; rm -f "$O/.orch.lock"
EOF
