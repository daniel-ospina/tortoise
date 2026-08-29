"""Neutral transport-context module (#1987 Task 6/8).

A zero-import module (stdlib only) defining the selfhost-transport signal
for the ask lane's metering/budget exemptions — a dedicated ContextVar
channel that both ``tortoise.sdk``/``tortoise.metering``/``tortoise.quota``
and the MCP auth layer can import without a cycle (``mcp_auth`` imports
``tortoise.sdk``, so ``sdk``/``metering``/``quota`` cannot import
``tortoise.mcp_auth``).

Why a dedicated flag (not the team_id VALUE, not ``_transport_mode``):

  * hosted team ids are RAW — only graph names are ``team_``-prefixed
    (``graph_name = f\"team_{team_id}\"``, hosted_api.py) — so a hosted team
    legitimately named \"selfhost\" delivers the IDENTICAL team_id value to
    the identical call site; keying the exemption on the value cannot
    distinguish hosted from selfhost.
  * the existing ``_transport_mode`` ContextVar is set to ``\"http\"`` on BOTH
    the hosted MCP path AND the selfhost MCP path — it cannot distinguish
    them either.

The flag is set True ONLY by the selfhost HTTP MCP transport
(``TransportModeMiddleware.dispatch`` — a named Task 8 deliverable on
``tortoise/mcp_auth.py``), the only selfhost transport whose team_id is
truthy (\"selfhost\"). The stdio bootstrap and the selfhost REST handler
rely on ``not team_id`` (team_id=None) and do NOT need the flag.
"""
from __future__ import annotations

from contextvars import ContextVar

#: True while a SELFHOST HTTP MCP transport is serving the request. Read by
#: ``record_ask_usage`` (tortoise/metering.py) and the shared ask budget
#: helper (tortoise/quota.py) alongside ``not team_id`` as the exemption
#: condition. Set ONLY by selfhost transport code.
_selfhost_transport: ContextVar[bool] = ContextVar("_selfhost_transport",
                                                   default=False)
