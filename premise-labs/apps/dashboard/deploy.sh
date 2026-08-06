#!/bin/bash
# Deploy Tortoise Dashboard to Cloudflare Pages (app.premiselabs.co)
set -e
cd "$(dirname "$0")"
npm run build
npx wrangler pages deploy dist --project-name=tortoise-dashboard --branch=main
