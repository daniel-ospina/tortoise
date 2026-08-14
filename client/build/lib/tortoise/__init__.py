"""Tortoise — thin client namespace (tortoise-client distribution, #526).

This is the CLIENT-ONLY `tortoise` namespace: it ships ONLY the modules a
network driver needs (mcp_client + config + exceptions) so that
``import tortoise.mcp_client`` works in a client-only environment. It is
deliberately NOT the engine's `tortoise/__init__.py` (which imports
redislite and the SDK guard machinery) — the engine stays in the
`tortoise-graph` distribution, installed on the server side.

The thin driver is also exposed under the cleaner `tortoise_client`
namespace (re-export) — client-first code should prefer
``from tortoise_client import status``.

License: Apache-2.0 (see client/LICENSE) — this distribution re-licenses
only the shipped client modules; the engine remains BSL-1.1 (#526).
"""

__version__ = "0.2.0"  # mirrors client/pyproject.toml; lockstep with tortoise-graph minor
