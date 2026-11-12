#!/usr/bin/env bash
# ensure-aws-cli.sh — guarantee the R2 runtime dependency the backup driver has (#12135).
#
# ⚠ NOT the driver's only external dependency: it also shells out to `jq` (55 sites)
# and `curl` (43), neither of which anything verifies — a missing `jq` is currently
# reported as a false `R2_UNLISTABLE` storage fault. Same class, tracked in #12208.
#
# `.github/scripts/registry-cron.sh` shells out to `aws s3api` for every R2
# operation it performs: the create-once dedup object, the direct freshness
# listings, and every head/get/put/delete. Nothing ever installed that binary —
# the design inherited it from the runner image, and the plan said so:
#   docs/plans/2026-08-08-596-plan.md:229
#     "the driver uses the `aws` CLI (preinstalled on ubuntu runners)"
#
# That is true of GitHub-hosted `ubuntu-latest` (AWS CLI v2 ships in the image)
# and FALSE of this fleet's self-hosted pool, which advertises the `ubuntu-latest`
# label without the CLI. Measured (run 43208529968, runner pi-runner-cmux-x64-5):
# `aws: command not found` at registry-cron.sh:397, so the R2 preflight's R2_DOWN
# filing could not write its dedup object, EVERY filing that hour was refused,
# and the job went red 20 minutes later inside a *dedup* error that blamed
# "conditional write rejected".
#
# So the dependency is DECLARED here and the driver VERIFIES it (its own
# preflight refuses to run the R2 legs when it is absent).
#
# Idempotent by design, because both runner families have to land here:
#   * a runner that already has a working `aws` pays NOTHING — no network, no
#     download, no install (GitHub-hosted runners: always this branch);
#   * a self-hosted runner installs a PINNED, sha256-verified build ONCE into
#     $RUNNER_TOOL_CACHE, which persists across jobs on that host.
#
# The pin is deliberate: this runs inside an hourly DR cron, so an unverified
# download would put a third party's current bytes on the path that decides
# whether backups and DR alerts work. A withdrawn/rotated version fails this step
# LOUDLY (see the `--check` failure) rather than silently installing something
# else; update PIN_VERSION + both digests together, in one reviewed change.
set -euo pipefail

PIN_VERSION="2.31.27"
# sha256 of https://awscli.amazonaws.com/awscli-exe-linux-<arch>-${PIN_VERSION}.zip
# as fetched from AWS's own installer URL on 2026-11-12 (the zips are 59-62 MB).
PIN_SHA256_X86_64="b947648b49e5cf53ee4b71b88d61bc6af6bfddbf3c372d668efa24380e9fd101"
PIN_SHA256_AARCH64="38e3fc18e43707ea74a6dbfada8216d08e385723db488d4164a7e35426f32e2c"

log() { echo "ensure-aws-cli: $*"; }
fail() { echo "ensure-aws-cli: ERROR: $*" >&2; }

# ── 1. already satisfied? ───────────────────────────────────────────────────
# `command -v` alone is not enough: a shim that exists and cannot EXECUTE fails
# identically one call later, so probe the binary.
if command -v aws >/dev/null 2>&1 && aws --version >/dev/null 2>&1; then
  log "already installed — $(aws --version 2>&1 | head -1) — nothing to do"
  exit 0
fi

# ── 2. architecture (the pool carries x86_64 AND arm64 runners) ─────────────
case "$(uname -m)" in
  x86_64|amd64)  arch="x86_64";  pin_sha="$PIN_SHA256_X86_64" ;;
  aarch64|arm64) arch="aarch64"; pin_sha="$PIN_SHA256_AARCH64" ;;
  *)
    fail "unsupported architecture '$(uname -m)' — no pinned AWS CLI build for it"
    exit 1 ;;
esac

# ── 3. a previous job's install on this host? ───────────────────────────────
root="${RUNNER_TOOL_CACHE:-${HOME}/.cache}/aws-cli-${PIN_VERSION}-${arch}"
if [ -x "${root}/bin/aws" ] && "${root}/bin/aws" --version >/dev/null 2>&1; then
  log "reusing ${root} — $("${root}/bin/aws" --version 2>&1 | head -1)"
  if [ -n "${GITHUB_PATH:-}" ]; then echo "${root}/bin" >> "$GITHUB_PATH"; fi
  exit 0
fi

# ── 4. install the pinned build ────────────────────────────────────────────
# ⛔ A prior attempt may have left a PARTIAL tree, and the installer will NOT
# repair it (review P1): `aws/install` creates $root/v2/<version> BEFORE it copies
# anything, so a cancelled / timed-out / OOM-killed run leaves a tree that the
# reuse probe above rejects (no working bin/aws) and the installer then reads as
# already installed — it prints "Found same AWS CLI version … Skipping install",
# exits 0, and the post-check below fails. Under `continue-on-error` on the step
# that is a PERMANENT wedge on that host that nothing else surfaces. Discard it
# here, as soon as the probe has proved it unusable: nothing that still worked is
# destroyed (a working tree returned above), and doing it BEFORE the download
# keeps the remedy deterministic — one code path, one place, observable.
rm -rf "$root"

for tool in curl unzip sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || { fail "'$tool' is required to install the AWS CLI and is missing"; exit 1; }
done

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
zip="awscli-exe-linux-${arch}-${PIN_VERSION}.zip"
url="https://awscli.amazonaws.com/${zip}"

log "downloading ${url}"
# The download is bounded as a WHOLE LEG, not per attempt (review P2): the job's
# 45-min ceiling has only ≈12 min of headroom above the driver's own legs
# (1500+300+120+20+20 ≈ 1960s), and `--retry 3 --max-time 300` allowed ~20 min of
# retries — a HANGING mirror (blackholed egress, which is exactly the "no egress"
# condition this step exists to survive) would burn the ceiling and the runner
# would kill the driver mid-sweep: no heartbeat, no filing, no self-heal. 120s per
# attempt with 2 retries is ≈6 min worst case, and the step is additionally bounded
# by `timeout 300` in the workflow.
curl --fail --silent --show-error --location --connect-timeout 10 \
  --retry 2 --retry-delay 2 --max-time 120 \
  "$url" --output "${work}/${zip}"

if ! echo "${pin_sha}  ${work}/${zip}" | sha256sum --check --status -; then
  fail "sha256 MISMATCH for ${zip} — refusing to install it (expected ${pin_sha}, got $(sha256sum "${work}/${zip}" | awk '{print $1}')). A rotated or tampered archive must fail here, not be installed; update the pin in this file deliberately."
  exit 1
fi
log "sha256 verified"

unzip -q "${work}/${zip}" -d "$work"
mkdir -p "${root}/bin"
# The bundled installer writes ${root}/v2/<version> and symlinks ${root}/bin/aws
# into it. `--update` is accepted for a repeat install into the same tree.
"${work}/aws/install" --bin-dir "${root}/bin" --install-dir "$root" --update >/dev/null

if ! "${root}/bin/aws" --version >/dev/null 2>&1; then
  fail "the installer reported success but ${root}/bin/aws does not run — remove ${root} and re-run (this step recreates it from the pinned archive)"
  exit 1
fi
log "installed — $("${root}/bin/aws" --version 2>&1 | head -1)"

if [ -n "${GITHUB_PATH:-}" ]; then
  echo "${root}/bin" >> "$GITHUB_PATH"
else
  log "GITHUB_PATH is unset (not running in Actions) — add ${root}/bin to PATH yourself to use it"
fi
