# tortoise-client

Thin network driver for the **Tortoise** epistemic graph engine — the
client side of the [#526 client/server package split](https://github.com/daniel-ospina/tortoise/issues/526).

Tortoise runs **as a service** — hosted or self-hosted — and this package is
the **driver** that connects to it over MCP (Model Context Protocol). It is
the MongoDB-driver model: the engine (`tortoise-graph`) lives on the server;
this package only ever **connects**, never imports engine code.

```text
your script / agent  ──MCP──►  tortoise-server (tortoise-graph)
   tortoise-client        (self-hosted daemon or hosted api.premiselabs.co)
```

- **Thin:** ships only the MCP driver (`mcp_client`) + shared config/types
  and the recorded status vocabulary (`status_vocabulary`).
  No engine, no FalkorDB, no numpy/scipy/fastapi.
- **Permissive:** Apache-2.0 (engine stays BSL-1.1 — the license boundary
  sits at the network, so a client-only install never touches BSL code).
- **Zero server deps:** installing this package does not pull the engine or
  its dependencies.

## Install

Requires **Python ≥ 3.12**.

```bash
pip install tortoise-client
```

> The bare `tortoise` name on PyPI is taken by an unrelated turtle-graphics
> package — the engine dist is `tortoise-graph`, the client dist is
> `tortoise-client`.

> **⚠️ Do NOT install `tortoise-client` and `tortoise-graph` in the same
> environment.** Both distributions install a top-level `tortoise` package;
> co-installing overwrites files and breaks the engine. Install the client
> where you **connect** from, and the engine (`tortoise-graph`) on the
> machine that **serves** — they talk over MCP and never share an
> interpreter.

## Connect

Point the client at a running Tortoise server. Self-hosted: run the server
([docs/quickstart-selfhosted.md](https://github.com/daniel-ospina/tortoise/blob/main/docs/quickstart-selfhosted.md) —
`docker compose up -d` or `tortoise-serve`), then:

```bash
export TORTOISE_MCP_URL=http://localhost:8000/mcp   # default
# export TORTOISE_API_KEY=tt_...                   # set if the server requires auth
```

Hosted: use your hosted endpoint and API key (`https://api.premiselabs.co/mcp/`).

## CLI

```bash
tortoise-client status               # connectivity + tool-count probe (never raises)
tortoise-client list-tools           # tool names exposed by the server
tortoise-client call tortoise_query '{"kind": "statement"}'
```

## Python API

```python
from tortoise_client import status, available, list_tools, call_tool

status()  # {"status": "ok", "url": ..., "tools": N}
available()  # True when the server is reachable
tools = list_tools()  # ["tortoise_create_point", "tortoise_query", ...]
result = call_tool("tortoise_create_point", {"kind": "statement", "content": "X is Y"})
```

The compatibility namespace `tortoise.mcp_client` is also provided
(identical module — `from tortoise.mcp_client import status` works).

`status()`/`available()` degrade gracefully — **this describes the library
surface**: a down server reports `tortoise_unavailable` instead of raising, so
script callers skip cleanly. The `tortoise-client status` CLI **probe** presents
its result in the **one recorded client-boundary status vocabulary**
(#3805 / roadmap §7 item 9, ADOPTED 2026-09-17) under a distinct process exit
code (`0` · `3` · `4` · `1` · `2`) — #526's *exit 0 on degradation* clause is
**superseded for the CLI probe only** (#3832 / D5, 2026-09-17). The library
keeps its never-raise contract.

### The client-boundary status vocabulary

The published term set is `tortoise.status_vocabulary` — four terms, each
naming exactly one condition, and no two of them collapsible into each other:

| term | condition it names | probe exit code |
|---|---|---|
| `available` | the store was reached and returned content | `0` |
| `empty` | the store was reached and returned nothing | `0` |
| `degraded` | the store is **configured but could not be reached** — off by *outage* | `3` |
| `unconfigured` | **no store / endpoint / provider key is configured** — off by *policy*, a set-up gap | `4` |

The load-bearing property: `empty` (the store answered and had nothing) is never
reported as `degraded` or `unconfigured` (the store did not answer), and neither
failure is ever reported as a successful empty result. `degraded` and
`unconfigured` are never reported as each other either — a broken memory must
not look empty, and a set-up gap must not blame the service.

The words `ok` / `tortoise_unavailable` / `not_configured` — the pre-#3805
payload words — are **superseded by these four** (the exit codes are unchanged).
A caller that has not migrated reads them through
`status_vocabulary.resolve(word, configured=…)`; the probe still translates,
never re-mints.

`status_vocabulary.LEGACY_WORDS` publishes only the words a flat lookup can
answer (`ok` → `available`, `not_configured` → `unconfigured`).
`tortoise_unavailable` is deliberately **not** in that table: the driver reports
it for *any* failure to answer and has no notion of a missing endpoint, so its
term depends on whether an endpoint was ever declared and a table cannot carry
it. Resolve it with the configuration fact (`LEGACY_WORDS_NEEDING_CONFIGURATION`
names it): the same word is `unconfigured` (exit `4`) when no endpoint is
declared and `degraded` (exit `3`) when one is — the never-configured vs
configured-but-down split this contract exists to keep, and the reason a bare
`LEGACY_WORDS` lookup must never be the documented path for it.

**Both client surfaces speak these four terms**, from that ONE declaration
(imported, not copied): this thin probe, and the S9 skill-wiring client
`tortoise/tortoise_client.py` — whose `status` payload and error values moved
to `available` / `degraded` / `unconfigured` here, with its exit codes
(`0`/`3`/`4`) unchanged.

⚠️ **One raised divergence in the read path.** The read-path half of this
contract is an unmerged branch (`tortoise/read_status.py` on
`feat/3892-read-path-status` / PR #4040). It uses the same four terms but maps
them differently: it takes `unconfigured` to mean *"no store configured **or**
unreachable"* and `degraded` to mean *"a leg did not run"*, where this boundary
takes `degraded` to mean the **configured-but-unreachable** (outage) condition.
The same word would then name two conditions, and the distinction #3832 / D5
exists to protect (*never configured* vs *configured but down*) is collapsed on
the read path. Flagged for resolution — not silently aligned. See
`tortoise/status_vocabulary.py` for the full note.

Only the `status` probe emits `3`/`4`; `list-tools` and `call` are operations
against a declared endpoint, so any failure there keeps the generic code `1`.
On the probe, `TORTOISE_MCP_URL` **unset** *is* the definition of unconfigured —
a self-hoster running the default daemon without declaring the variable reads
as `unconfigured` (exit `4`) when that daemon is down. Declare the endpoint
(set the variable, even to the default) to get the `degraded` state.

## What's inside

| Module | Purpose |
|---|---|
| `tortoise/mcp_client.py` | The network driver — fastmcp Client + BearerAuth + StreamableHttpTransport (`status`/`available`/`list_tools`/`call_tool`), sync wrappers over fastmcp's async API |
| `tortoise/config.py` | Shared config constants + env conventions (connection vars live in `mcp_client`: `TORTOISE_MCP_URL` / `TORTOISE_API_KEY`) |
| `tortoise/exceptions.py` | Shared error taxonomy surfaced across the tool boundary |
| `tortoise/status_vocabulary.py` | The **one recorded status vocabulary** (`available` / `empty` / `degraded` / `unconfigured`) — roadmap §7 item 9; the single declaration both client surfaces import |
| `tortoise_client/` | Client-first shim package re-exporting the driver API + the `tortoise-client` CLI |

**Not included (by design):** `tortoise.sdk`, `tortoise.projection`,
`tortoise.ep`, the daemon, the MCP server, and every engine dependency.
A clean install of this package cannot import any engine module.

## Version coupling

`tortoise-client` and `tortoise-graph` release in **lockstep with the same
version number**, and a client of minor version `X.Y` targets a server of
the same minor `X.Y` (the MCP tool surface is additive within a minor — the
server never removes or renames tools mid-minor). Both dists pin the same
`fastmcp==3.4.6` protocol version. Policy details:
[docs/client-server-split.md §Version coupling](https://github.com/daniel-ospina/tortoise/blob/main/docs/client-server-split.md).

## License

Apache-2.0 — see [LICENSE](LICENSE). This distribution contains only the
thin client modules, re-licensed permissively (MongoDB/Redis driver
precedent); the engine remains under BSL-1.1 in the `tortoise-graph`
distribution.
