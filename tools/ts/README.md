# Repo TypeScript tooling (not part of the deployed site)

`tsconfig.website-functions.json` typechecks **only** `website/functions/**` — the
Cloudflare Pages Functions tree (agent write API, admin gate, cache purge, contact
form). `website-functions-typecheck` in `.github/workflows/ci.yml` runs it on every
PR that touches that tree, this directory, or `ci.yml`.

## Why the config does not live next to the code it checks (#4311)

Everything under `website/` is **staged for public upload**. `deploy-pages.yml`
builds the upload root with

```bash
rsync -a --exclude='/apps/' --exclude='/migrations/' --exclude='node_modules/' \
         --exclude='/.wranglerignore' --exclude='*.md' website/ "$STAGE/"
```

so a `tsconfig.json` / `package.json` / `package-lock.json` placed under
`website/functions/` would be served at `/functions/tsconfig.json`,
`/functions/package.json`, … on the live site. That is the same reason the Pages
binding inventory lives in `config/required-bindings.yml`
(`tests/test_pages_bindings.py::test_the_manifest_is_not_inside_the_pages_upload_root`
pins that rule for manifest-class files). Repo tooling that is not part of the
deployed site therefore lives here, outside the upload root.

We deliberately did **not** add the three paths to the deploy step's exclude list:
that would mean editing the staging shell, the classification fixture and the leak
probe (the "nested internal file" case `deploy-pages.yml` documents) to publish
nothing — more moving parts than moving the tooling one directory out.

## Why the toolchain is pinned

`package.json` names exact `typescript` and `@cloudflare/workers-types` revisions and
`package-lock.json` is committed; CI installs with `npm ci`. A floating range would
let the strictness of this gate change with no reviewed diff — a green-to-red (or
red-to-green) CI flip that no PR explains. `tests/test_website_functions_typecheck.py`
pins the wiring.

## Running it locally

```bash
cd tools/ts
npm ci
npm run typecheck:website-functions
```
