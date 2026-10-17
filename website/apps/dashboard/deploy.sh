#!/bin/bash
# Deploy Tortoise Dashboard to Cloudflare Pages (app.premiselabs.co)
set -e
# #8816: resolve this script's own directory ONCE, to an ABSOLUTE path, before any
# `cd`. `$0` may be RELATIVE (`bash website/apps/dashboard/deploy.sh`), so
# re-deriving `$(dirname "$0")` AFTER the `cd` below double-applies the prefix —
# the read then points at a path that does not exist and the manual deploy aborts.
# An absolute SCRIPT_DIR is correct from any cwd and for every invocation.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
# #3768: node_modules is no longer version-controlled, so a clean checkout must
# install first; without this the build fails instead of silently using a stale
# tracked tree.
if [ ! -d node_modules ]; then npm ci; fi
npm run build
# #8816: the version has ONE home. The path climbs from the dashboard app to the
# repo root, and we ASSERT what npm resolved: a declared "4" resolves to 4.151.1
# and fails, where a bare `npx wrangler` would silently deploy whatever is latest.
WRANGLER_VERSION="$(cat "$SCRIPT_DIR/../../../config/wrangler-version")"
: "${WRANGLER_VERSION:?config/wrangler-version is empty}"
got="$(npx --yes "wrangler@${WRANGLER_VERSION:?}" --version | tr -d '[:space:]')"
if [ "$got" != "$WRANGLER_VERSION" ]; then
  echo "::error::resolved wrangler ${got} != declared ${WRANGLER_VERSION}"
  exit 1
fi
npx --yes "wrangler@${WRANGLER_VERSION:?}" pages deploy dist --project-name=tortoise-dashboard --branch=main
