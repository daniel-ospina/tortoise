#!/bin/bash
# Deploy Tortoise Dashboard to Cloudflare Pages (app.premiselabs.co)
set -e
# Resolve THIS script's directory once, absolutely, and use it for both the `cd`
# and the version read below. Re-deriving `$(dirname "$0")` AFTER the `cd` applies
# the directory component twice, so the invocation docs/infra-runbook.md documents
# (`./website/apps/dashboard/deploy.sh` from the repo root) looked for
# website/apps/dashboard/../../../config/wrangler-version and died in `cat`
# before the assertion could ever run (#8852 review P1).
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
# #3768: node_modules is no longer version-controlled, so a clean checkout must
# install first; without this the build fails instead of silently using a stale
# tracked tree.
if [ ! -d node_modules ]; then npm ci; fi
npm run build
# #8816: the version has ONE home, config/wrangler-version, read through the SAME
# absolute directory this script runs in — and ASSERT what npm resolved: a
# declared "4" resolves to 4.151.1 and fails, where a bare `npx wrangler` would
# silently deploy whatever is latest.
WRANGLER_VERSION="$(cat "$HERE/../../../config/wrangler-version")"
: "${WRANGLER_VERSION:?config/wrangler-version is empty}"
got="$(npx --yes "wrangler@${WRANGLER_VERSION:?}" --version | tr -d '[:space:]')"
if [ "$got" != "$WRANGLER_VERSION" ]; then
  echo "::error::resolved wrangler ${got} != declared ${WRANGLER_VERSION}"
  exit 1
fi
npx --yes "wrangler@${WRANGLER_VERSION:?}" pages deploy dist --project-name=tortoise-dashboard --branch=main
