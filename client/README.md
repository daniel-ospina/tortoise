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

- **Thin:** ships only the MCP driver (`mcp_client`) + shared config/types.
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

status()                       # {"status": "ok", "url": ..., "tools": N}
available()                    # True when the server is reachable
tools = list_tools()           # ["tortoise_create_point", "tortoise_query", ...]
result = call_tool("tortoise_create_point",
                   {"kind": "statement", "content": "X is Y"})
```

The compatibility namespace `tortoise.mcp_client` is also provided
(identical module — `from tortoise.mcp_client import status` works).

`status()`/`available()` degrade gracefully: a down server reports
`tortoise_unavailable` instead of raising.

## What's inside

| Module | Purpose |
|---|---|
| `tortoise/mcp_client.py` | The network driver — fastmcp Client + BearerAuth + StreamableHttpTransport (`status`/`available`/`list_tools`/`call_tool`), sync wrappers over fastmcp's async API |
| `tortoise/config.py` | Shared config constants + env conventions (connection vars live in `mcp_client`: `TORTOISE_MCP_URL` / `TORTOISE_API_KEY`) |
| `tortoise/exceptions.py` | Shared error taxonomy surfaced across the tool boundary |
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
