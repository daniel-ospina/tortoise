#!/usr/bin/env bash
# npm-global-wedge.sh — heal a global npm prefix wedged by ONE interrupted install.
#
# ⛔ WHY THIS FILE EXISTS — read this before changing a line of it.
#
#   `npm install -g <pkg>` never extracts over the old copy. Arborist first RETIRES
#   the existing directory: it renames `<prefix>/node_modules/<pkg>` aside to
#   `workspaces/arborist/lib/retire-path.js`'s name,
#
#       `.<basename>-<first 8 alnum chars of base64(sha1(<absolute path>))>`
#
#   which is a PURE FUNCTION OF THE PATH — not random. When an install is killed
#   between the retire and the move-back (a job cancelled at its cap, a runner
#   restarted mid-fetch, a timeout), that retired directory SURVIVES. Every LATER
#   install of that package in that prefix then computes the SAME destination, and
#   `rename(2)` returns ENOTEMPTY when the destination is an existing non-empty
#   directory. So the later install dies instantly — and a backoff retry loop around
#   it re-runs the identical rename, so it dies three times for one reason. That is
#   not a flake: sleeping cannot clear it, and the job that inherits it cannot pass.
#
#   MEASURED 2026-11-02T03:58Z, the required `legal-e2e` check on `gh-runner-x64-7`:
#   three attempts (~11 s, ~22 s, ~30 s apart) ALL naming the identical destination
#   `/opt/gh-runner-x64-7/_work/_tool/node/22.23.4/x64/lib/node_modules/.wrangler-UdmJz4wm`.
#   That suffix reproduces exactly from the formula above for that runner's path —
#   the determinism IS the failure mode, so it is quoted here as the worked example.
#   The red belongs to a state the job did not create, and it would red every PR
#   scheduled onto that runner until someone logged in and removed the directory.
#
# USAGE
#   npm-global-wedge.sh clear <absolute package dir>
#       remove that package's retired residue in the SAME parent directory.
#       Prints `CLEARED <path>` per removal, `NO-STALE-STATE <dir>` when clean.
#   npm-global-wedge.sh --help
#
# EXIT — a caller must be able to tell "there was nothing to heal" from "I could
# not heal it", because reading on as if the prefix were healthy is how the next
# `npm install -g` fails with an error that names npm instead of the runner:
#   0  nothing to heal, or everything that was stale is gone
#   1  usage error — unknown verb, no argument, or a RELATIVE path
#   2  a residue is still there after the attempt: the next install of that
#      package WILL fail ENOTEMPTY, and the message names the residue and the host
#
# SCOPE — deliberately narrow, stated rather than hidden:
#   * Only `<parent>/.<basename>-*` is touched, i.e. retired residue for THIS
#     package in THIS prefix. The package directory itself is left alone: npm
#     retires it happily once the stale destination is out of the way, so deleting
#     it would be a wider action than the wedge requires.
#   * A glob, not a recomputed hash: the residue to remove is "whatever npm left
#     next to this package", and a re-implementation of the hash in shell could
#     disagree with npm's and would silently miss the very directory that reds the
#     build. Dot-prefixed package directories are not installable, so no legitimate
#     sibling can be caught by it.
#   * A single runner instance executes one job at a time and owns its own tool
#     prefix (`_work/_tool/...`), so a residue is a PREVIOUS job's, never a live
#     peer's in-flight rollback. Running two concurrent global installs into one
#     prefix is a pre-existing hazard this script does not attempt to arbitrate.

set -uo pipefail

usage() {
  sed -n 's/^# \{0,1\}//p' "$0" | sed -n '/^USAGE/,/^SCOPE/p' | sed '$d'
}

clear_package() {
  local target="$1"
  local dir base cleaned=0 residue

  case "$target" in
    /*) ;;
    *)
      echo "::error::npm-global-wedge: '$target' is not an absolute path — a relative" \
        "path would heal the wrong prefix (measured: npm's retired name is a hash of" \
        "the ABSOLUTE path)" >&2
      return 1
      ;;
  esac

  dir="$(dirname "$target")"
  base="$(basename "$target")"

  if [ ! -d "$dir" ]; then
    # No prefix at all: nothing can be wedged yet, and the install that follows
    # creates it. Not an error — refuse to invent one.
    echo "NO-STALE-STATE ${dir} (absent)"
    return 0
  fi

  shopt -s nullglob
  # shellcheck disable=SC2206  # the glob is the contract; word-splitting is intended
  local residues=("$dir/.${base}-"*)
  shopt -u nullglob

  if [ ${#residues[@]} -eq 0 ]; then
    echo "NO-STALE-STATE ${dir}"
    return 0
  fi

  for residue in "${residues[@]}"; do
    rm -rf -- "$residue"
    if [ -e "$residue" ]; then
      echo "::error::npm-global-wedge: could not remove ${residue} on ${RUNNER_NAME:-$(hostname)}" \
        "— the next 'npm install -g' will fail ENOTEMPTY, because npm retires" \
        "${target} to exactly that name and rename(2) refuses a non-empty" \
        "destination. Remove it by hand on the runner host; retrying the install" \
        "cannot clear it." >&2
      return 2
    fi
    echo "CLEARED ${residue}"
    cleaned=$((cleaned + 1))
  done

  # Touched for the reader: the count is the only summary a log needs.
  echo "healed ${cleaned} stale npm residue(s) for ${base}"
  return 0
}

main() {
  case "${1:-}" in
    clear)
      [ $# -eq 2 ] || { usage >&2; return 1; }
      clear_package "$2"
      ;;
    -h | --help)
      usage
      ;;
    *)
      echo "npm-global-wedge: unknown verb '${1:-}'" >&2
      usage >&2
      return 1
      ;;
  esac
}

main "$@"
