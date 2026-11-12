set -euo pipefail
# ⛔ THE PACKAGE COMES FROM PGDG, NOT THE DISTRO ARCHIVE (#12051).
# `ubuntu-latest` is a MOVING label. Measured 2026-11-12 (job
# `131100353978`, run `43180078623`, main `7e6baffc5d`): it resolved to
# `ubuntu-26.04`, whose archive carries the CURRENT distro major (18 in
# that release) and NOT the 16 this job needs, so the first cut of this
# step died before the leg ran —
# `E: Unable to locate package postgresql-client-<the major>`, exit 100. The
# distro archive can never be relied on for an OLD major; the job would
# red on a runner-image roll rather than on a diff, which is the class
# #12051 exists to remove. apt.postgresql.org publishes EVERY supported
# major for every supported release (`16.15-1.pgdg26.04+2` on resolute,
# `16.15-1.pgdg24.04+2` on noble — both verified in a container running
# THIS script), so the pin resolves the same way wherever the label goes.
# Identity must survive BOTH runner classes: `ImageOS`/`ImageVersion` are set ONLY
# on GitHub-hosted runners, so naming them alone reports `unknown` on a self-hosted
# box — and a self-hosted box is exactly the #10289 path these messages exist for.
# `RUNNER_*` is set by the agent on both, and `RUNNER_ENVIRONMENT` says which remedy
# applies. A remedy that is already the state ("pin the job") is not a remedy there.
identity="${RUNNER_ENVIRONMENT:-unknown} ${RUNNER_NAME:-unknown} ${RUNNER_OS:-unknown}"
if [ -n "${ImageOS:-}" ]; then
  identity="$identity hosted-image=${ImageOS}/${ImageVersion:-unknown}"
fi
# ⛔ FIVE NAMED FAILURE CLASSES. This is an enumeration OF THE GUARDS, not a claim
# that nothing else can fail: the keyring WRITES above and `. /etc/os-release` are
# left to `set -e`, because each of them fails CLOSED already and re-wrapping every
# write would be ceremony rather than a better diagnostic (a `No space left on
# device` on the keyring write, or a `sudo` needing a password on a pool box, still
# exits non-zero — it is just not named). What IS named below is every class where
# the underlying cause is the ARTIFACT'S CONTRACT rather than the box's health, since
# those are the ones that read as a typo in this step:
#   1. the signing key cannot be FETCHED (egress/DNS/503) — guarded at the fetch;
#   2. the box carries no VERSION_CODENAME, so there is no suite to name — guarded below;
#   3. the repo does not RESOLVE once added (suite name, key, archive) — `apt-get update`;
#   4. the repo resolves but has no `postgresql-client-<major>` — the probe below;
#   5. the package RESOLVES but cannot be INSTALLED — the install itself, named below.
# ⛔ (5) IS NOT PEDANTRY on a POOL box: this job is pool-eligible, and a shared box
# can hold the dpkg frontend lock (the image's own unattended-upgrades) or run out of
# disk, both of which fail the INSTALL after a clean probe — and would otherwise
# surface as apt's text, i.e. as the un-named class these guards exist to replace.
# ⚠️ The key fetch, the `apt-get update` and the install are ALL fatal on purpose:
# swallowing any of them would let the step fall back to the DISTRO archive, which
# carries whatever major the release ships and is the mechanism #12051 round 1 died on.
if ! curl -fsSL -o /tmp/pgdg.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc; then
  echo "::error::could not FETCH the PGDG signing key (ACCC4CF8.asc) [$identity] — the client this drill needs comes from apt.postgresql.org, so without the key nothing can be installed and BOTH provisioned servers are pg16 (#12051). Check network egress from this runner to www.postgresql.org."
  exit 1
fi
# The keyring's DIRECTORY is created here rather than assumed: PGDG publishes it inside
# `postgresql-common`, which a bare runner image does not carry, so `install -m 0644`
# below would fail `No such file or directory` — unnamed, one line before the guard.
sudo install -d /usr/share/postgresql-common/pgdg
sudo install -m 0644 /tmp/pgdg.asc /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
. /etc/os-release
# ⛔ `${VERSION_CODENAME}` IS CHECKED, not left to `set -u`: an unbound-variable abort
# at the sources line is exactly the unnamed failure the three guards exist to replace,
# and a box whose os-release carries no codename is a state this step can NAME.
if [ -z "${VERSION_CODENAME:-}" ]; then
  echo "::error::/etc/os-release on this runner carries no VERSION_CODENAME [$identity] — the PGDG suite name cannot be derived, so the pg16 client this drill needs cannot be installed (#12051). A Debian/Ubuntu box always sets it; a non-apt-derived image does not."
  exit 1
fi
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" | sudo tee /etc/apt/sources.list.d/pgdg.list >/dev/null
if ! sudo apt-get update -qq; then
  echo "::error::apt-get update FAILED after the PGDG source was added (suite ${VERSION_CODENAME:-unknown}-pgdg) [$identity] — the repository or its signing key did not resolve, so the pg16 client this drill needs cannot be installed, and BOTH provisioned servers are pg16 (#12051). Check the key at /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc, the suite name derived from /etc/os-release, and network egress."
  exit 1
fi
if ! apt-cache show postgresql-client-16 >/dev/null 2>&1; then
  # ⛔ THIS BRANCH'S REMEDY IS NAMED FROM ITS OWN CAUSE, not from release drift.
  # MEASURED, review cycle 4 of `3c2946ce9c`: the committed step exits 0 on
  # `ubuntu:26.04` — the very "box is not the release that claims the label"
  # case — because `${VERSION_CODENAME}-pgdg` is resolute's own suite and PGDG
  # publishes 16 for it; and a release PGDG does NOT publish (no suite) exits
  # 100 at `apt-get update`, i.e. the guard ABOVE. So #10289 routing drift cannot
  # land here, and the old `--repair-labels` remedy here was advice that could
  # not change apt's answer.
  echo "::error::a PGDG suite RESOLVED but carries no postgresql-client-16 for this codename [$identity] — apt-get update succeeded and the probe still found nothing, so this is the PACKAGE, not the repo. BOTH provisioned servers are pg16, so this job cannot run here (#12051): either the pin and the two 'pgvector/pgvector:pg16' servers must move together, or '${VERSION_CODENAME:-this release}' is a codename PGDG publishes no PG16 package for. Check 'apt-cache policy postgresql-client-16' and the suite derived from /etc/os-release."
  exit 1
fi
if ! sudo apt-get install -y --no-install-recommends postgresql-client-16; then
  echo "::error::postgresql-client-16 RESOLVED but could not be INSTALLED [$identity] — the probe above succeeded, so this is apt/dpkg failing at the install (a shared box holding the dpkg frontend lock, a .deb fetch that broke mid-run, or disk exhaustion), not a missing package. BOTH provisioned servers are pg16, so this drill cannot run without the client (#12051)."
  exit 1
fi
# PREPENDED: the versioned binaries must win over the image's own
# client and over `postgresql-common`'s unversioned wrapper.
echo "/usr/lib/postgresql/16/bin" >> "$GITHUB_PATH"
export PATH="/usr/lib/postgresql/16/bin:$PATH"
for tool in pg_dump pg_restore; do
  version="$("$tool" --version)"
  printf '%s\n' "$version"
  major="$(printf '%s\n' "$version" | sed -E 's/[^0-9]*([0-9]+)\..*/\1/')"
  if [ "$major" != "16" ]; then
    echo "::error::$tool is major $major, but BOTH provisioned servers are pg16 — an artifact dumped by a newer client carries a GUC the target cannot execute (#12051). The whole record leg would red for a reason that has nothing to do with the diff."
    exit 1
  fi
done
