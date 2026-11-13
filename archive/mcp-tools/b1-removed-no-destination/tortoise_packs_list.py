# Archived mcp-tool `tortoise_packs_list` — REMOVED from the product surface (#11609).
#
# batch:   b1-removed-no-destination
# reason:  no destination in the approved 27-tool surface; no replacement (owner ruling, #11609)
# source:  tortoise/tool_registry.py @ 73c479ce405b3561b5e3abd321d910a5fccb9622
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'mcp-tool'
ARCHIVED_SYMBOL = 'tortoise_packs_list'
ARCHIVED_BATCH = 'b1-removed-no-destination'
ARCHIVED_FROM = 'tortoise/tool_registry.py'
ARCHIVED_AT = '73c479ce405b3561b5e3abd321d910a5fccb9622'

# ── verbatim from tortoise/tool_registry.py ──

    ToolDefinition(
        name="tortoise_packs_list",
        id="surface.packs_list",
        description="List this team's active packs (#318): the shared pack catalog "
                    "joined with the tenant graph's PackInstall activation records. "
                    "NOT a pure read: when the graph carries no PackInstall record "
                    "at all, the handler re-seeds the starter set before it answers "
                    "(one MERGE per starter namespace), so a graphs:read caller can "
                    "cause a write. The re-seed is skipped when the graph's pack set "
                    "was deliberately configured (#8648). Empty result when nothing "
                    "is installed (existence masking — another tenant's packs are "
                    "never observable).",
        risk="maintenance",
        http_policy=True,
        sdk_method="",  # custom handler in mcp_server.py (calls pack_state.get_tenant_packs)
    ),

# ── verbatim from tortoise/mcp_server.py ──

def tortoise_packs_list() -> list[dict]:
    """List this org's ACTIVE packs (#318 — multi-tenant pack isolation).

    Shared pack catalog + the tenant graph's PackInstall activation records
    (ensure-then-read core shared with REST GET /v1/packs — no REST/MCP
    divergence). Auth-only scoping via the _current_org_id contextvar seam:
    cross-tenant access is structurally impossible. D6 masking: empty result
    when nothing is installed (never an error); the only error surface is
    auth/transport failure.
    """
    # Fail-closed (#318): in HTTP/tenant mode a missing org context must
    # NEVER fall back to the base (default-namespace) SDK for pack
    # introspection — pack state is per-tenant. (stdio/selfhost keeps the
    # base SDK: single-tenant, the base graph IS the tenant.)
    if _current_org_id.get() is None and _transport_mode.get() == "http":
        return _SafeError({"error": "Authentication required. No team context.",
                           "code": ERR_UNAUTHORIZED})
    from tortoise.pack_state import get_tenant_packs
    return _safe(lambda: get_tenant_packs(_get_org_sdk()))
