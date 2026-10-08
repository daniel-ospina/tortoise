#!/bin/bash
# Deploy Tortoise Dashboard to Cloudflare Pages (app.premiselabs.co)
set -e
cd "$(dirname "$0")"
# #3768: node_modules is no longer version-controlled, so a clean checkout must
# install first; without this the build fails instead of silently using a stale
# tracked tree.
if [ ! -d node_modules ]; then npm ci; fi
npm run build
npx wrangler pages deploy dist --project-name=tortoise-dashboard --branch=main
