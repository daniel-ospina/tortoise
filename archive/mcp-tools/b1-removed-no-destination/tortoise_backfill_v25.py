# Archived mcp-tool `tortoise_backfill_v25` — REMOVED from the product surface (#11609).
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
ARCHIVED_SYMBOL = 'tortoise_backfill_v25'
ARCHIVED_BATCH = 'b1-removed-no-destination'
ARCHIVED_FROM = 'tortoise/tool_registry.py'
ARCHIVED_AT = '73c479ce405b3561b5e3abd321d910a5fccb9622'

# ── verbatim from tortoise/tool_registry.py ──

    ToolDefinition(
        name="tortoise_backfill_v25",
        id="surface.backfill_v25",
        description="Backfill database to ONTOLOGY v2.5 schema. "
                    "EXCLUDED from tenant HTTP — schema-level migration (operator-only).",
        risk="destructive",
        http_policy=False,
        sdk_method="backfill_v25",
    ),

# ── verbatim from tortoise/mcp_server.py ──

def tortoise_backfill_v25(dry_run: bool = True) -> dict:
    """Backfill database to ONTOLOGY v2.5 schema.

    #236: EXCLUDED from tenant HTTP — schema-level migration (operator-only).
    """
    if _transport_mode.get() == "http":
        return _http_excluded_error()
    return _safe(_get_org_sdk().backfill_v25, dry_run=dry_run)
