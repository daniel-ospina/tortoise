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
| **Client** | `tortoise-client` (new) | Apache-2.0 | thin MCP driver (`tortoise/mcp_client.py`), the stdio bridge (`tortoise/mcp_bridge.py`, #10605), shared config + error types, the recorded status vocabulary (`tortoise/status_vocabulary.py`, #3805), minimal CLI |

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
  shared_modules.sh        # derives the wheel's shared-module set from
                           #   build_client.sh (the CI path gate + allowlists read it)
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
- `tortoise/mcp_bridge.py` — the stdio ↔ Streamable-HTTP bridge (canonical
  copy, #10605 — see §8a)
- `tortoise/config.py` — shared config (canonical copy)
- `tortoise/exceptions.py` — shared error types (canonical copy)
- `tortoise/status_vocabulary.py` — the one recorded status vocabulary
  (`available` / `empty` / `degraded` / `unconfigured`, #3805; canonical copy)
- `tortoise/__init__.py` — client namespace shim (`__version__` only)
- `tortoise_client/` — re-export shim + CLI

The canonical-copy list is not transcribed anywhere by hand: the `cp` lines
in `client/build_client.sh` are the source, and `client/shared_modules.sh`
derives the set from them for the CI `client` path gate and both wheel
allowlists (PR #4044 review).

**What stays server-side (unchanged):** everything else — sdk.py,
projection, ep, FalkorDB deps, fastapi/uvicorn, mcp_server, mcp_auth/
session_auth, hosted/self-host APIs, billing, ingest/index CLIs,
`tortoise/tortoise_client.py` (the S9 skill-wiring CLI — **not** a network
driver, deliberately not the basis of the client; it remains an internal
server-side wrapper. Only its machine-readable status WORDS moved, to the
recorded vocabulary in `tortoise/status_vocabulary.py` — #3805; the split
itself is unchanged).

**No breakage:** `tortoise-graph` keeps shipping the full `tortoise.*`
tree exactly as before — `from tortoise.mcp_client import ...` keeps working
(no shim needed; the module never moved). The split is purely additive.

## 4. Dependency split

| Distribution | Dependencies |
|---|---|
| `tortoise-graph` (server) | fastapi, httpx, uvicorn, numpy, scipy, falkordb, falkordblite, pyyaml, fastmcp, prometheus-client, posthog (+ extras) |
| `tortoise-client` (client) | `fastmcp-slim[client]==3.4.6`, `httpx>=0.27` |

- `fastmcp-slim[client]` provides the exact `fastmcp.client` import surface
  `mcp_client.py` uses (Client + StreamableHttpTransport, with the Bearer supplied
  by a per-request `httpx.Auth` provider — never `BearerAuth`, which CAPTURES the
  string; §8a)
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

## 8a. Credentials in a long-lived session — the bridge (#10605)

**The defect this closes.** A harness config that carries an `Authorization`
header is a CAPTURE, not a lookup — and the capture has two halves, only one of
which is the `${VAR}` syntax. The EXPANSION is not the stale part: on the
harness that reported this, `expandExpr()` **re-runs at every connect**
(measured from its source twice, and on the wire with 3/3 `initialize` POSTs,
see #10605). What is frozen is `process.env` itself, captured when the harness
process starts, plus the per-CONNECTION resolution: the header a config yields
is resolved once per connection and replayed for that connection's life, so a
mid-connection rotation is invisible until the next one. An UNSET variable
expands silently to `""`, so a session whose credential is exported under a
name the client never reads sends an `Authorization: Bearer` header with
nothing after the scheme, on every connect — the shape that produced the
reported 401.

So the server can only answer `Unauthorized: invalid or missing Bearer token`.
That message is accurate and useless: it points at the credential's VALUE when
the name, or the source, was never read again after the process started. The
design consequence is a CONFIG-NAME fix, not a cache-expiry fix (#10605; #5200 is
the same class reached by a variable-name mismatch).

**The fix, in two layers.**

1. `tortoise/mcp_client.py` resolves the Bearer value **per request** (an httpx
   `auth_flow` provider — the `token()`-callback shape the MCP SDKs converge on,
   rather than a captured string), reading `TORTOISE_API_KEY`, then the fleet
   alias `TORTOISE_MCP_API_KEY`, then the stored credentials in the recorded
   #1708 D5 order: the PROJECT-LOCAL `./.tortoise` that `tortoise init --api-key`
   writes, then `~/.tortoise/credentials.json`. A store that ARRIVES WITH a
   repository is skipped rather than read — git reports it tracked, or git cannot
   be asked and a `.git` sits above it (see the trust boundary below); an ordinary
   untracked store is read. A rotation
   therefore reaches the wire on the next call with no rebuild and no restart. A
   blank value is "no credential", never an `Authorization: Bearer` header with
   nothing after the scheme; if the endpoint then rejects the call, the client raises
   `MCPKeyEmptyError` (code `MCP-KEY-EMPTY`) naming every source it read instead
   of relaying the server's wording. `status()` reports that as the recorded
   `unconfigured` term (a set-up gap, not an outage, #3805). The named refusals
   hold on the PUBLIC constructor too (`get_client()` returns a guarded client),
   not only on `status`/`available`/`list_tools`/`call_tool`.

   A credential whose CO-SOURCED host is not this client's endpoint is
   **withheld**, never sent, and reported as `MCPCredentialHostMismatchError`
   (`MCP-CREDENTIAL-HOST-MISMATCH`) — and only for an auth rejection: a daemon
   that is down is an outage whatever the credential situation. Endpoints are
   compared as endpoints (scheme, host, default port, path without a trailing
   slash), so one endpoint spelled two ways never reads as two hosts.
2. `tortoise/mcp_bridge.py` is a local **stdio ↔ Streamable-HTTP** bridge, so a
   harness can declare a command instead of a header:

   ```json
   {"mcpServers": {"tortoise": {
       "command": "python3", "args": ["-m", "tortoise.mcp_bridge"],
       "env": {"TORTOISE_MCP_URL": "https://api.premiselabs.co/mcp/"}}}}
   ```

   No key in the config, no `${VAR}` to expand, nothing to go stale. Its auth
   refusals are named too, and split by cause: `MCP-KEY-EMPTY` when nothing
   resolved (fixable without a restart), `MCP-CREDENTIAL-NOT-CARRIED` when one
   resolved but the request that failed carried none — the cross-origin redirect
   hop, after which httpx deliberately drops `Authorization` — versus a
   value-fault message when a credential WAS sent and the server refused it. Each
   cause carries the remedy that can actually work for its source: an env key is
   pointed at the final URL, a stored key is given the `api_url` to record,
   because a stored credential takes its endpoint from its own file (#2369 D1.1).
   Server text is quoted as evidence, but a credential VALUE echoed back by the
   server is redacted before it reaches the harness's transcript or logs.

   One operational knob: `TORTOISE_MCP_BRIDGE_TIMEOUT` is the seconds allowed per
   forwarded request (default 120, declared in `.env.example`). It is generous on
   purpose because a `tortoise_search` carries the server's own wait budget; a
   legitimately slow call is answered by raising it, not by reading the resulting
   timeout as an outage. A malformed value falls back to the default.

**WHICH SOURCE IS ACTUALLY LIVE — stated, because "reads the live environment" is
too generous.** The bridge is a CHILD process, so its environment is a snapshot
of the harness's at spawn: a key exported in another shell after the session
started is not visible to it. Re-reading the environment helps a process that
sets its own `os.environ` mid-run (a notebook, a login flow); the source that
genuinely rotates under a long-lived session — and the one a hosted user has on
disk, written by `tortoise init` — is the **credential file**, re-read per
request. The env leg is precedence, not liveness. Both stores are read: a user
who onboarded through the CLI has the project-local one, and a client that read
only the global file would report "no credential" for a key sitting on disk.
**One exception, and it is a trust boundary rather than a precedence rule: a
store that ARRIVES WITH a repository is IGNORED.** Both halves of the identity
come from one file (`api_key` and `api_url`), so a repository that commits
`./.tortoise` would choose the key *and* the host its requests go to — the shape
`#3660` fails closed on. Precisely: a store git TRACKS is skipped, and so is one
inside a git work tree when git cannot be run at all to say whether it is tracked
(the fail-closed arm; a store in no repository, and an ordinary untracked store
in one, are read as before). The global store is vetted the same way whenever a
`.git` sits above it. A store `tortoise init` writes is gitignored, so the
ordinary project-local store is unaffected *where git can answer*; the ignored one
is named in the refusal's source list and warned about once on stderr.

**CO-SOURCE (#2369 D1.1).** When the key comes from the credential file, the
endpoint comes from that SAME file (`api_url`), never from a `TORTOISE_MCP_URL`
the environment supplies: a harness config can be committed to a repository, so
letting it choose the host for a stored credential would be an exfiltration path.

**What this does NOT do (residual — recorded on #10605, tracked OPEN on #10885).** The
onboarding generator (`tortoise/__main__.py::_harness_mcp_config`) and this repo's committed
`.mcp.json` still emit the `Authorization: Bearer ${TORTOISE_API_KEY}` capture for
the **hosted** shape. That is deliberate and product-facing, not an oversight:
the hosted recipe must work for a user who has installed *nothing* locally, and
the bridge needs a Python package present. Switching those recipes — and the
fleet's own harness configs, which agent-infra owns — is the follow-up that
retires the capture everywhere; until it lands the bridge is the recipe for
anyone with the client installed, and the named refusal is what everyone else now
gets instead of a misleading 401.

⛔ **#10605 is CLOSED, so it is not the live home for that residual — #10885 is.**
A reader checking whether the capture has been retired must follow **#10885**
(OPEN, filed with this residual's `file:line` scope and its acceptance criterion); #10605
records only where the residual came from. The same relay applies to the
`503 consent_store_unavailable` arm and the keyless-re-entry hole, which now live
on **#10884** rather than in #10487's closed thread.

The two halves are held by DIFFERENT pins, and this paragraph names them
separately rather than claiming one covers both. The #10885 sentence is held by
`tests/test_mcp_bridge.py::test_the_hosted_recipe_residual_has_a_home_this_fold_does_not_close`,
whose biconditional reads this `**What this does NOT do**` paragraph itself. The
`#10884` half is held by the two pins in `tests/test_oauth_consent_bff_routes.py` —
`test_the_store_fault_residual_names_a_home_this_fold_does_not_close` (that
module's docstring) and
`test_the_whitespace_attempt_key_residual_names_a_home_this_fold_does_not_close`
(`presented_consent_attempt_key`'s own docstring) — because #10884's two residuals
live in the CODE, not on this page: they red when the arm or the unstripped
adoption goes away, and they cannot read a markdown paragraph at all.

Both modules are staged into the client-only wheel (`client/build_client.sh`), and
both have guards registered on the `api` CI surface:
`tests/test_mcp_client.py` (`TestCredentialResolution`) and
`tests/test_mcp_bridge.py` — the latter drives a real bridge subprocess and
asserts the header the UPSTREAM received, so "resolved per request" is measured
on the wire rather than inferred.

## 9. Migration

- **New users/scripts:** install `tortoise-client` and connect to a server
  (hosted endpoint or self-hosted daemon) — the BSL engine never reaches
  their machine.
- **Existing users:** nothing breaks — `tortoise-graph` is byte-identical in
  behavior; `tortoise.mcp_client` remains importable from the server
  package.
- **Internal consumers** (skills, graph-scripts, tests, bridge) migrate at
  leisure — the daemon is already their integration point (post-#338/#554).
- `tortoise/tortoise_client.py` (S9 skill wiring) is not part of the split and
  stays server-side; only its status words moved to the recorded vocabulary
  (`tortoise/status_vocabulary.py`, #3805).
