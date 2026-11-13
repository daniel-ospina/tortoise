# Archived mcp-tool `tortoise_pack_install` — REMOVED from the product surface (#11609).
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
ARCHIVED_SYMBOL = 'tortoise_pack_install'
ARCHIVED_BATCH = 'b1-removed-no-destination'
ARCHIVED_FROM = 'tortoise/tool_registry.py'
ARCHIVED_AT = '73c479ce405b3561b5e3abd321d910a5fccb9622'

# ── verbatim from tortoise/tool_registry.py ──

    ToolDefinition(
        name="tortoise_pack_install",
        id="surface.pack_install",
        description="Install a custom expansion pack on the HOSTED surface (#1935): "
                    "validates against the shared registry + tenant policy "
                    "(reserved starter namespace, ontology-only v1), stores the "
                    "manifest in the tenant graph and activates it. Deployment- "
                    "gated: on self-host this is an actionable stub (use the "
                    "filesystem packs dir + tortoise pack CLI).",  # C5 #2114 (re-review P2): MERGEs manifests/installs — a write
        risk="admin",
        http_policy=True,
        sdk_method="",  # custom handler in mcp_server.py (calls pack_manifest_store.upsert_tenant_manifest)
        group="admin",
        hosted_only=True,
    ),

# ── verbatim from tortoise/mcp_server.py ──

def tortoise_pack_install(manifest_yaml: str) -> dict:
    """#1935: install a custom expansion pack on the HOSTED surface.

    Validates against the shared registry validator (schema + cross-pack)
    plus the tenant policy (reserved starter namespace + ontology-only v1),
    stores the manifest graph-natively in the tenant's graph
    (``:PackManifest``) and activates it (idempotent).

    DEPLOYMENT-GATED (#1935 R10): on SELF-HOST this tool is an actionable
    stub — self-host configures packs via the filesystem packs dir
    (``TORTOISE_PACKS_DIR``) + the ``tortoise pack`` CLI instead. The gate
    is the serving app: ``tortoise.hosted_api`` is only imported in the
    hosted deployment.
    """
    # C5 #2114 (final-gate P2): packs are DEFAULT-graph org-level state —
    # graph-bound keys rejected (REST twin upload_pack_manifest parity; a
    # per-graph install would orphan pack state list_packs never sees).
    _reject_graph_bound_mcp_org_surface("pack install")
    import sys as _sys

    from tortoise.pack_manifest_store import upsert_tenant_manifest
    if _sys.modules.get("tortoise.hosted_api") is None:
        return _SafeError({
                       "installed": False,
                       "error": "tortoise_pack_install is HOSTED-only — on self-host add "
                                 "custom packs via the filesystem packs dir "
                                 "(TORTOISE_PACKS_DIR) + the 'tortoise pack' CLI.",
                   })
    if _transport_mode.get() != "http":
        return _SafeError({"installed": False,
                           "error": "pack install requires the HTTP transport."})
    try:
        record = upsert_tenant_manifest(_get_org_sdk(), manifest_yaml)
    except ValueError as e:
        # A REJECTED manifest is a refusal, even though it carries no `error`
        # key (`validation_errors` names the reason). Left as a plain dict the
        # seam recorded the dispatch `ok` (#9609 review): the exact-payload arm
        # of `_returned_safe_failure` classifies it once it IS an `_SafeError`,
        # and the wire shape does not move (dict subclass).
        return _SafeError({"installed": False,
                           "validation_errors": [str(e)]})
    return {"installed": True, **record}
