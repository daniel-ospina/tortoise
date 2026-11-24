#!/usr/bin/env bash
# pip-wedge.sh — heal a Python prefix wedged by ONE interrupted `pip install`.
#
# ⛔ WHY THIS FILE EXISTS — read this before changing a line of it.
#
#   `pip install -e .` REPLACES the distribution: it uninstalls the installed copy
#   before it writes the new one. That uninstall MOVES files aside first, and it
#   does so in the prefix itself — pip's stash names are siblings that begin with
#   `~` (`~ortoise-graph`, and on a partial move the bare `~`). When a job is
#   killed between the move-aside and the clean-up (a cancel at the job cap, a
#   runner restart, a timeout), BOTH halves survive in that interpreter's
#   `site-packages`:
#
#     * the `~…` stash directories, which pip can never adopt (it calls them
#       "invalid distribution" and skips them, so they accumulate forever), and
#     * a `*.dist-info` for the package that lost its `RECORD` file.
#
#   Every LATER `pip install -e` in that prefix then dies, and it is NOT a flake:
#   pip finds the distribution, reads its version as `None`, and refuses to
#   uninstall something whose contents it cannot enumerate. Retrying recomputes
#   the same refusal, because nothing about the prefix changed between attempts.
#
#   MEASURED 2026-11-24T04:09:32Z — the REQUIRED `legal-e2e` job, step
#   `Run pip install -e '.[test]'`, on the pool runner `pi-runner-vm-2`
#   (run 45323932558, job 138208901025), verbatim:
#
#     WARNING: Ignoring invalid distribution ~ortoise-graph (…/site-packages)
#     Found existing installation: tortoise-graph None
#     error: uninstall-no-record-file
#     × Cannot uninstall tortoise-graph None
#     ╰─> The package's contents are unknown: no RECORD file was found for tortoise-graph.
#
#   The residue is a PREVIOUS job's: it lives in the shared `setup-python` tool
#   cache (`_work/_tool/Python/<ver>/<arch>`), so it is inherited by every job the
#   scheduler puts on that runner — the red belongs to a state the job did not
#   create, and it reds every PR until someone logs in and removes the directories.
#
#   SIBLING: `.github/scripts/npm-global-wedge.sh` (#10523) heals the SAME class of
#   wedge for a global npm prefix — same job, same pool, same lesson: a red that no
#   retry can clear needs a heal step, not a longer backoff.
#
# USAGE
#   pip-wedge.sh clear <dist-name>            # heal the prefix the `python` on PATH uses
#   pip-wedge.sh clear --at <dir> <dist-name> # heal an EXPLICIT prefix (tests, other pythons)
#   pip-wedge.sh --help
#
#   <dist-name> is the distribution as pip knows it — `tortoise-graph`, not the
#   import name `tortoise`. It is normalised the way pip normalises it
#   (`-`/`.` → `_`, lowercased), which is also how the dist-info directory is named.
#
# EXIT — a caller must be able to tell "there was nothing to heal" from "I could not
# heal it", because reading on as if the prefix were healthy is how the next
# `pip install -e` fails with an error that names pip instead of the runner:
#   0  nothing to heal, or everything that was stale is gone
#   1  usage error — unknown verb, missing argument, or a dist name that is not a
#      distribution name. Also: the prefix could not be resolved at all.
#   2  a residue is still there after the attempt: the next `pip install -e` of this
#      distribution WILL fail `uninstall-no-record-file`, and the message names the
#      residue and the host.
#
# SCOPE — deliberately narrow, stated rather than hidden:
#   * `~…` entries are removed wholesale, not matched by name: pip's stash name is
#     built from the path it is moving, the residue to remove is "whatever pip left
#     next to this distribution", and a re-implementation of pip's naming in shell
#     could disagree with pip's and would silently miss the very directory that reds
#     the build. A distribution directory whose name starts with `~` is not a
#     distribution (pip itself refuses to read it), so no legitimate install is
#     caught by it.
#   * A `dist-info`/`egg-info` for the NAMED distribution is removed ONLY when it has
#     no `RECORD`. One that has a `RECORD` is a distribution pip can uninstall
#     itself, and deleting it would be a wider action than the wedge requires.
#   * Nothing else in the prefix is touched — no other distribution, and not the
#     package's own import directory (a stale `tortoise/` is overwritten by the
#     install that follows; removing it would be a wider action than the wedge needs).

set -uo pipefail

usage() {
  sed -n 's/^# \{0,1\}//p' "$0" | sed -n '/^USAGE/,/^SCOPE/p' | sed '$d'
}

# pip's own normalisation of a project name: runs of `-`/`_`/`.` collapse to `_`.
normalise_dist() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[-._]\+/_/g'
}

resolve_purelib() {
  local py out
  for py in python3 python; do
    command -v "$py" >/dev/null 2>&1 || continue
    out="$("$py" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])' 2>/dev/null)" || continue
    if [ -n "$out" ]; then
      printf '%s\n' "$out"
      return 0
    fi
  done
  echo "::error::pip-wedge: no python on PATH could resolve its purelib — the heal" \
    "cannot be aimed at a prefix, so the install that follows is UNHEALED" >&2
  return 1
}

clear_prefix() {
  local dir="$1" dist="$2" cleaned=0 residue
  local norm
  norm="$(normalise_dist "$dist")"

  if [ ! -d "$dir" ]; then
    # No prefix at all: nothing can be wedged yet, and the install that follows
    # creates it. Not an error — refuse to invent one.
    echo "NO-STALE-STATE ${dir} (absent)"
    return 0
  fi

  shopt -s nullglob

  # (a) pip's stash residue. `~*` is pip's own naming for a move-aside in progress
  #     or abandoned; nothing installable is named this way.
  for residue in "$dir"/~*; do
    rm -rf -- "$residue"
    if [ -e "$residue" ]; then
      echo "::error::pip-wedge: could not remove ${residue} on ${RUNNER_NAME:-$(hostname)}" \
        "— the next 'pip install -e' will fail 'uninstall-no-record-file', because pip" \
        "reads this stash as an invalid distribution and every later install collides" \
        "with it. Remove it by hand on the runner host; retrying the install cannot" \
        "clear it." >&2
      return 2
    fi
    echo "CLEARED ${residue}"
    cleaned=$((cleaned + 1))
  done

  # (b) a dist-info/egg-info for THIS distribution that survived without its RECORD.
  #     pip reports this one as `<dist> None` and then refuses to uninstall it.
  for residue in "$dir/${norm}"-*.dist-info "$dir/${norm}"-*.egg-info "$dir/${norm}".egg-info; do
    [ -e "$residue" ] || continue
    # A dist-info WITH a RECORD is a distribution pip can uninstall itself —
    # leaving it is the narrower action, and it is what makes a clean prefix
    # report NO-STALE-STATE rather than a heal it did not need.
    [ -f "${residue}/RECORD" ] && continue
    rm -rf -- "$residue"
    if [ -e "$residue" ]; then
      echo "::error::pip-wedge: could not remove ${residue} on ${RUNNER_NAME:-$(hostname)}" \
        "— it has no RECORD, so the next 'pip install -e' will fail" \
        "'uninstall-no-record-file' naming ${dist}. Retrying the install cannot clear it." >&2
      return 2
    fi
    echo "CLEARED ${residue} (no RECORD)"
    cleaned=$((cleaned + 1))
  done

  shopt -u nullglob

  if [ "$cleaned" -eq 0 ]; then
    echo "NO-STALE-STATE ${dir}"
    return 0
  fi
  echo "healed ${cleaned} stale pip residue(s) for ${dist} in ${dir}"
  return 0
}

main() {
  local dir=""
  case "${1:-}" in
    clear)
      shift
      if [ "${1:-}" = "--at" ]; then
        [ $# -eq 3 ] || { usage >&2; return 1; }
        dir="$2"
        shift 2
        case "$dir" in
          /*) ;;
          *)
            echo "::error::pip-wedge: --at '$dir' is not an absolute path — a relative" \
              "path would heal the wrong prefix" >&2
            return 1
            ;;
        esac
      else
        [ $# -eq 1 ] || { usage >&2; return 1; }
        dir="$(resolve_purelib)" || return 1
      fi
      case "${1:-}" in
        "" | -* | */*)
          echo "::error::pip-wedge: '${1:-}' is not a distribution name (expected e.g." \
            "'tortoise-graph')" >&2
          return 1
          ;;
      esac
      clear_prefix "$dir" "$1"
      ;;
    -h | --help)
      usage
      ;;
    *)
      echo "pip-wedge: unknown verb '${1:-}'" >&2
      usage >&2
      return 1
      ;;
  esac
}

main "$@"
