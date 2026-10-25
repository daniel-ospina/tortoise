#!/usr/bin/env bash
# runner-tools.sh — THE RUNNER TOOL CONTRACT (#9018).
#
# ⛔ WHY THIS FILE EXISTS — read this before changing a line of it.
#
#   `runs-on: ubuntu-latest` names a GitHub-HOSTED IMAGE. That image ships a
#   documented tool set — `gh`, `unzip`, `jq`, `docker`, `git` — and jobs were
#   written against it.
#
#   The fallback pool (`pi-runner-vm-*`, `pi-runner-x64-*`, `pi-runner-cell*-x64-*`)
#   carries the SAME label while `scripts/ci_runner_gate.sh` has the fallback
#   engaged, so the scheduler routes `ubuntu-latest` jobs onto it. A label is a
#   scheduling decision; it is NOT an image. The runner is a bare agent on a
#   host, and its environment is whatever started the service — a NON-login,
#   minimal `PATH`. `gh` installed for an interactive login shell is invisible
#   there.
#
#   The result is not a test failure. It is a red that LOOKS like one:
#
#     line 2: gh: command not found
#     ##[error]Process completed with exit code 127.
#
#   measured 2026-10-24 on `canary-streak` and 38 of 39 `finding-provenance`
#   `classify` runs — every one of them on a pool runner, and none of them on
#   GitHub-hosted. Worse, a `gh` call wrapped as `if ! out="$(gh … 2>/dev/null)"`
#   swallows the `command not found` on stderr and re-reports the blindness as
#   its own honest-looking verdict ("heartbeat search failed", "CI: UNKNOWN"),
#   so the runner's fault is mis-attributed to the instrument.
#
#   THE CONTRACT THIS FILE ENFORCES: a job that needs a tool of the hosted image
#   must OBTAIN it in the job (or fail with a message that names the tool and
#   the runner) — never inherit an assumption about the machine it landed on.
#
#   ⛔ DO NOT "FIX" A RED BY LOOSENING AN ASSERTION. If a job needs `gh`, the
#   answer is `ensure gh`; a skip or a `|| true` around a failed `gh` call turns
#   a blind check into a green one, which is strictly worse than a red.
#
# USAGE
#   runner-tools.sh ensure gh                 # provide gh in THIS job, then prove it resolves
#   runner-tools.sh ensure gh unzip jq        # each: present, or provisioned, or a named failure
#   runner-tools.sh assert gh unzip docker    # check only — never installs
#   runner-tools.sh --help
#
# EXIT — the caller must be able to tell "the tool is absent" from "my attempt to
# provide it failed", because collapsing them is how a blind check reads as a
# verdict:
#   0  every requested tool resolves
#   1  UNSATISFIED — the tool is absent and NOTHING was installed. The
#      `RUNNER-TOOL-CONTRACT-UNSATISFIED` line names the tool(s) and the runner.
#      (`offline-and-absent`, `unsupported-arch`, a tool with no provisioner.)
#   2  COULD-NOT-PROVIDE / COULD-NOT-CHECK — an attempt WAS made and failed, or
#      the check itself could not run: download, sha256 mismatch, extract, link,
#      installed-but-unresolvable, or a malformed seam. Nothing was certified.
#   1  is also the usage error for an unknown verb / no tool named, which the
#      caller never reaches from a workflow (both are writer bugs, not runner
#      state) — stated here so the table is complete rather than convenient.
#
# WHY `gh` IS PINNED. A `gh` that happens to be present is used as found — that
# is the fast path on GitHub-hosted, and this script does not version-check it.
# But WHEN THIS SCRIPT MUST PROVISION, what gets installed is decided here, in
# review: the release is pinned by version AND sha256 below, so the fallback path
# is not decided by whatever a host happens to have on disk.
set -uo pipefail

# Pinned from `GET /repos/cli/cli/releases/latest` on 2026-10-24: version, plus
# the sha256 GitHub publishes per release asset. Bumping the version means
# replacing BOTH digests — a stale digest is a hard refusal, never a warning.
GH_VERSION="2.103.2"
GH_SHA256_AMD64="62ccf747115ddc61237efffdde66a3c8f70ba249433dc64f70801089b59c59c8"
GH_SHA256_ARM64="4c3e99a50bd8b181a3fe9d69b25dd978bac768148d88f01507c6b4d1ef61a443"

say() { printf 'runner-tools: %s\n' "$*"; }
unsatisfied() { printf 'RUNNER-TOOL-CONTRACT-UNSATISFIED %s\n' "$*" >&2; }

runner_name() { printf '%s' "${RUNNER_NAME:-$(uname -n 2>/dev/null || echo unknown-runner)}"; }

resolve() { command -v "$1" 2>/dev/null || true; }

usage() { awk '/^# USAGE/{p=1} /^set -uo pipefail$/{p=0} p' "$0" | sed 's/^# \{0,1\}//'; }

host_arch() {
  case "$(uname -m)" in
    x86_64 | amd64) printf 'amd64' ;;
    aarch64 | arm64) printf 'arm64' ;;
    *) printf '' ;;
  esac
}

install_gh() {
  local arch sha dest url tmp got root
  arch="$(host_arch)"
  if [ -z "$arch" ]; then
    unsatisfied "tool=gh runner=$(runner_name) reason=unsupported-arch arch=$(uname -m)"
    return 1
  fi
  case "$arch" in
    amd64) sha="$GH_SHA256_AMD64" ;;
    arm64) sha="$GH_SHA256_ARM64" ;;
  esac
  # OPERATOR/TEST SEAM — a local archive plus ITS digest, for a box with no
  # egress. ⛔ The two move TOGETHER: a lone `RUNNER_TOOL_CONTRACT_GH_SHA256` is
  # refused, so the pin cannot be loosened on its own while the tarball still
  # comes from the pinned URL. No workflow sets either variable.
  if [ -n "${RUNNER_TOOL_CONTRACT_GH_SHA256:-}" ]; then
    if [ -z "${RUNNER_TOOL_CONTRACT_GH_TARBALL:-}" ]; then
      unsatisfied "tool=gh runner=$(runner_name) reason=sha256-override-without-tarball-seam"
      return 2
    fi
    sha="$RUNNER_TOOL_CONTRACT_GH_SHA256"
  fi

  dest="${RUNNER_TOOL_CONTRACT_BIN_DIR:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}/runner-tools}"
  mkdir -p "$dest" || {
    unsatisfied "tool=gh runner=$(runner_name) reason=cannot-create-dir dir=$dest"
    return 2
  }

  if [ -n "${RUNNER_TOOL_CONTRACT_GH_TARBALL:-}" ]; then
    tmp="$RUNNER_TOOL_CONTRACT_GH_TARBALL"
    [ -f "$tmp" ] || {
      unsatisfied "tool=gh runner=$(runner_name) reason=tarball-seam-missing path=$tmp"
      return 2
    }
  else
    if [ -n "${RUNNER_TOOL_CONTRACT_OFFLINE:-}" ] && [ "${RUNNER_TOOL_CONTRACT_OFFLINE}" != "0" ]; then
      # Absent, and nothing was installed: UNSATISFIED (1), not "attempt failed".
      unsatisfied "tool=gh runner=$(runner_name) reason=offline-and-absent"
      return 1
    fi
    url="https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${arch}.tar.gz"
    tmp="$dest/gh_${GH_VERSION}_linux_${arch}.tar.gz"
    curl -fsSL --retry 3 --retry-delay 2 --max-time 300 -o "$tmp" "$url" || {
      unsatisfied "tool=gh runner=$(runner_name) reason=download-failed url=$url"
      return 2
    }
  fi

  got="$(sha256sum "$tmp" | cut -d' ' -f1)"
  if [ "$got" != "$sha" ]; then
    # Never delete an OPERATOR-supplied archive: it lives outside $dest, and a
    # mistyped digest must not cost them the file they handed us.
    [ -n "${RUNNER_TOOL_CONTRACT_GH_TARBALL:-}" ] || rm -f "$tmp"
    unsatisfied "tool=gh runner=$(runner_name) reason=sha256-mismatch got=$got want=$sha"
    return 2
  fi

  # The release tarball's layout is `gh_<ver>_linux_<arch>/bin/gh` (+ share/).
  # Extract to `$dest/gh`, then link the one executable into `$dest/bin`, which
  # is the ONLY directory this script puts on PATH.
  rm -rf "${dest:?}/gh"
  mkdir -p "$dest/gh" "$dest/bin" || {
    unsatisfied "tool=gh runner=$(runner_name) reason=cannot-create-dir dir=$dest"
    return 2
  }
  tar xzf "$tmp" -C "$dest/gh" || {
    unsatisfied "tool=gh runner=$(runner_name) reason=extract-failed"
    return 2
  }
  root="$(find "$dest/gh" -maxdepth 3 -type f -name gh -perm -u+x 2>/dev/null | head -1)"
  if [ -z "$root" ]; then
    unsatisfied "tool=gh runner=$(runner_name) reason=archive-has-no-gh-binary"
    return 2
  fi
  ln -sf "$root" "$dest/bin/gh" || {
    unsatisfied "tool=gh runner=$(runner_name) reason=cannot-link dir=$dest/bin"
    return 2
  }
  [ -n "${RUNNER_TOOL_CONTRACT_GH_TARBALL:-}" ] || rm -f "$tmp"

  # ⛔ PROVE IT BEFORE PUBLISHING IT. `$GITHUB_PATH` is what every LATER step
  # reads, so it must never name a directory this script has just declared
  # unusable — a `continue-on-error` on a future caller would turn that into a
  # false pass. Verify against the new directory explicitly, then publish.
  local p
  p="$(PATH="$dest/bin:$PATH" command -v gh 2>/dev/null || true)"
  if [ -z "$p" ]; then
    unsatisfied "tool=gh runner=$(runner_name) reason=installed-but-unresolvable dir=$dest/bin"
    return 2
  fi

  # Two audiences, two mechanisms: `export PATH` reaches the remainder of THIS
  # script process only (the step is its own `bash` invocation), while
  # `$GITHUB_PATH` is drained by the runner into every LATER step's environment.
  # Neither one can substitute for the other.
  export PATH="$dest/bin:$PATH"
  if [ -n "${GITHUB_PATH:-}" ]; then
    printf '%s\n' "$dest/bin" >>"$GITHUB_PATH" || {
      unsatisfied "tool=gh runner=$(runner_name) reason=cannot-append-GITHUB_PATH"
      return 2
    }
  fi

  say "tool=gh action=installed version=$GH_VERSION path=$p runner=$(runner_name)"
  return 0
}

ensure_one() {
  local tool="$1" p
  p="$(resolve "$tool")"
  if [ -n "$p" ]; then
    say "tool=$tool action=present path=$p runner=$(runner_name)"
    return 0
  fi
  case "$tool" in
    gh) install_gh ;;
    *)
      # Nothing else in the contract can be installed without root, and a
      # silent partial install is worse than a refusal: name it and stop.
      unsatisfied "tool=$tool runner=$(runner_name) reason=absent-not-provisionable"
      return 1
      ;;
  esac
}

assert_one() {
  local tool="$1" p
  p="$(resolve "$tool")"
  if [ -n "$p" ]; then
    say "tool=$tool action=present path=$p runner=$(runner_name)"
    return 0
  fi
  unsatisfied "missing=$tool runner=$(runner_name) reason=absent"
  return 1
}

MODE="${1:-}"
shift || true
case "$MODE" in
  ensure | assert) ;;
  -h | --help | "")
    usage
    exit 0
    ;;
  *)
    printf 'runner-tools: unknown verb %s\n' "$MODE" >&2
    usage >&2
    exit 1
    ;;
esac

if [ "$#" -eq 0 ]; then
  printf 'runner-tools: %s needs at least one tool name\n' "$MODE" >&2
  exit 1
fi

rc=0
for t in "$@"; do
  if [ "$MODE" = "ensure" ]; then
    ensure_one "$t" || rc=$?
  else
    assert_one "$t" || rc=$?
  fi
  # A could-not-check (2) outranks an absence (1): report the most severe.
  [ "$rc" = 2 ] && break
done
exit "$rc"
