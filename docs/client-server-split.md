---
title: "Client/Server Package Split — tortoise-client thin driver (#526)"
type: engineering
domain: platform
doc_status: live
created: 2026-08-15
ownedBy: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-client, tortoise-graph
---

# Client/Server Package Split — `tortoise-client` thin driver (#526)

**Status:** implemented — additive split (2026-08-15).
**Decision:** MongoDB-analogy service model (owner-confirmed 2026-08-15): the
engine ships as a server-only distribution (`tortoise-graph`, BSL-1.1) and a
new thin driver distribution (`tortoise-client`, Apache-2.0) is the pip
surface for scripting/integrations. `pip install` no longer embeds the
BSL-licensed engine in the consumer's application.

## 1. Why

`pip install tortoise-graph` ships the ENTIRE engine (sdk.py ~11k lines,
projection, EP) under BSL-1.1. A >$5M organization that pip-installs for
scripting remains license-restricted on code it only drives over the
network. #338 made "connect, don't import" behaviorally true; this split
makes it physically true — the license boundary moves to the network.

## 2. Architecture — MongoDB-driver model

| Component | Distribution | License | Content |
|---|---|---|---|
| **Server** | `tortoise-graph` (existing, 0.2.0) | BSL-1.1 | engine (sdk, projection, EP), daemon, MCP server, hosted/self-host APIs, CLIs (`tortoise`, `tortoise-serve`, `tortoise-ingest`) |
| **Client** | `tortoise-client` (new) | Apache-2.0 | thin MCP driver (`tortoise/mcp_client.py`), shared config + error types, minimal CLI |

Industry precedent (verified 2026-08-13, issue comment): **MongoDB** (server
SSPL, all drivers Apache-2.0 — an app using the driver is "a separate
work"), **Redis** (client libraries stay open-source under RSAL/SSPL), and
**HashiCorp** (BSL-1.1 products, SDKs/libraries MPL-2.0). **Prefect**
(`prefect-client`) proves the monorepo mechanics in Python: a thin client
dist built from the same repo via a staging build path.

The bare `tortoise` PyPI name is taken (unrelated turtle-graphics package) —
the server dist keeps the published name `tortoise-graph`; `tortoise-client`
is free (verified).

## 3. Package structure — Prefect `prefect-client` pattern

**Decision: separate dist from the same repo via a `client/` build
directory** (NOT extras — extras cannot create a separate distribution, and
an extras-based client would still ship the engine wheel). This mirrors
Prefect's `client/` directory + `build_client.sh` staging mechanism.

```
client/
  pyproject.toml           # tortoise-client dist metadata (Apache-2.0, client-only deps)
  README.md                # client dist readme
  LICENSE                  # Apache-2.0 text (governs the client dist)
  build_client.sh          # staging build — copies shared modules from the canonical
                           #   repo tree, overlays client shims, builds wheel+sdist
  verify_client.sh         # acceptance gate (clean-venv import + dep checks)
  tortoise/__init__.py     # client-only `tortoise` namespace shim (lightweight —
                           #   NOT the engine's __init__, which imports redislite)
  tortoise_client/         # client-first shim package: re-exports the driver API
    __init__.py            #   + console script `tortoise-client`
    cli.py
    __main__.py
```

**How the build works** (mirrors Prefect's `build_client.sh`): stage a temp
tree, copy the CANONICAL shared modules from the repo's `tortoise/` package
(single source of truth — zero drift), overlay the client-only shim files,
then build with `python -m build` (falls back to `pip wheel`). Engine
modules are never staged. The wheel ships exactly:

- `tortoise/mcp_client.py` — the network driver (canonical copy)
- `tortoise/config.py` — shared config (canonical copy)
- `tortoise/exceptions.py` — shared error types (canonical copy)
- `tortoise/__init__.py` — client namespace shim (`__version__` only)
- `tortoise_client/` — re-export shim + CLI

**What stays server-side (unchanged):** everything else — sdk.py,
projection, ep, FalkorDB deps, fastapi/uvicorn, mcp_server, mcp_auth/
session_auth, hosted/self-host APIs, billing, ingest/index CLIs,
`tortoise/tortoise_client.py` (the S9 skill-wiring CLI — **not** a network
driver, deliberately not the basis of the client; it remains an internal
server-side wrapper).

**No breakage:** `tortoise-graph` keeps shipping the full `tortoise.*`
tree exactly as before — `from tortoise.mcp_client import ...` keeps working
(no shim needed; the module never moved). The split is purely additive.

## 4. Dependency split

| Distribution | Dependencies |
|---|---|
| `tortoise-graph` (server) | fastapi, httpx, uvicorn, numpy, scipy, falkordb, falkordblite, pyyaml, fastmcp, prometheus-client, posthog (+ extras) |
| `tortoise-client` (client) | `fastmcp-slim[client]==3.4.6`, `httpx>=0.27` |

- `fastmcp-slim[client]` provides the exact `fastmcp.client` import surface
  `mcp_client.py` uses (Client + BearerAuth + StreamableHttpTransport)
  **without** the server extra (uvicorn/websockets/cyclopts) and without
  fastapi/numpy/scipy/falkordb. The pin `==3.4.6` matches the server's
  `fastmcp==3.4.6` — protocol/API lockstep.
- `httpx>=0.27` is declared directly (transport floor — mirrors the server's
  own floor, #310/#494).
- **Acceptance:** a clean `pip install tortoise-client` must NOT pull
  falkordb / falkordblite / numpy / scipy / fastapi (enforced by
  `client/verify_client.sh`, wired into CI).
- websockets/uvicorn are intentionally NOT declared: the streamable-HTTP
  client transport runs over httpx; websockets/uvicorn belong to the
  server/SSE surface.

## 5. Version coupling

**Decision: matching minor versions, released in lockstep (Prefect
pattern).** Both dists carry the same version number at release
(`tortoise-client==0.2.0` alongside `tortoise-graph==0.2.0`), and:

- A client of minor `X.Y` targets a server of minor `X.Y`.
- The MCP tool surface is additive within a minor — the server never removes
  or renames tools mid-minor, so any patch `X.Y.z` client works against any
  patch `X.Y.z` server.
- Both dists pin the same protocol dependency (`fastmcp==3.4.6` /
  `fastmcp-slim[client]==3.4.6`), the strongest mechanical coupling.
- The client does NOT declare a pip dependency on `tortoise-graph` — the
  client is a separate install surface (client-only users never install the
  server); coupling is documented + enforced by CI drift checks.
- **Co-installation hazard (PR #1313 conf 78):** both dists install a
  top-level `tortoise` package. Installing `tortoise-client` AND
  `tortoise-graph` in the SAME environment overwrites files and breaks the
  engine — they are NOT co-installable. Client environments and server
  environments must stay separate, which matches the architecture: the
  client connects over MCP and never runs the engine.

## 6. License boundary

**Engine:** BSL-1.1 (unchanged — `LICENSE`, `$5M` AUG, MPL-2.0 conversion).

**Client: Apache-2.0.** Decision rationale (issue Q1, owner pick with legal
sign-off noted): Apache-2.0 is the **driver-industry norm** — MongoDB ships
every driver under Apache-2.0 explicitly so an application using the driver
is "a separate work" that never inherits server obligations; Redis keeps
client libraries open-source under its server licenses. Compared to
MPL-2.0 (HashiCorp's SDK precedent, file-level copyleft), Apache-2.0 is
maximally permissive — consumers can vendor, modify, and relicense the
driver without file-level obligations — which maximizes adoption for a thin
network driver and matches the MongoDB-driver analogy exactly. The engine's
BSL terms are untouched; a client-only install never ships BSL code.

Enforcement: `validation/check-license-surface.py` now also asserts the
client surfaces declare Apache-2.0 (client/LICENSE, client/pyproject.toml,
client/README.md) so the boundary cannot silently regress (same backstop
pattern as the engine's four-surface check).

## 7. CI wiring

- **`ci.yml` → `client-build` job (every PR):** builds the client wheel via
  `client/build_client.sh`, runs the full acceptance gate
  (`client/verify_client.sh` — clean-venv import + engine-dep checks), and
  verifies the wheel contains no engine modules. This is the per-PR guard
  that a future engine change can't leak into the client dist.
- **`publish-pypi.yml` → `build-client` + `publish-client` jobs (tagged
  release):** build + smoke the client wheel, upload as `dist-client`
  artifact, publish to PyPI via Trusted Publishing against the
  `pypi-client` environment (one-time setup: create the `tortoise-client`
  PyPI project + pending publisher for that environment — mirrors the
  existing `tortoise-graph` setup). The existing engine build/publish jobs
  are untouched.

## 8. Acceptance gate (runs green in this PR)

```bash
client/build_client.sh                          # -> dist-client/*.whl
client/verify_client.sh                         # clean-venv gate
```

| Check | Expect |
|---|---|
| `pip install tortoise-client` in a clean venv | succeeds; installs ONLY fastmcp-slim[client] + httpx (+ their transitive deps) |
| `import tortoise.mcp_client` | works |
| `import tortoise.sdk` / `import tortoise.projection` | raises ImportError |
| `pip list` | no falkordb / falkordblite / numpy / scipy / fastapi |
| Existing suites (`tests/test_mcp_client.py`, `tests/test_tortoise_client.py`) | pass against `tortoise-graph` (unchanged server tree) |

## 9. Migration

- **New users/scripts:** install `tortoise-client` and connect to a server
  (hosted endpoint or self-hosted daemon) — the BSL engine never reaches
  their machine.
- **Existing users:** nothing breaks — `tortoise-graph` is byte-identical in
  behavior; `tortoise.mcp_client` remains importable from the server
  package.
- **Internal consumers** (skills, graph-scripts, tests, bridge) migrate at
  leisure — the daemon is already their integration point (post-#338/#554).
- `tortoise/tortoise_client.py` (S9 skill wiring) is untouched and stays
  server-side.
