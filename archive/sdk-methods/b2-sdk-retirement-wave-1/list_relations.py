# Archived sdk-method `list_relations` — REMOVED from the product surface (#11609).
#
# batch:   b2-sdk-retirement-wave-1
# reason:  Listed pack-declared relation declarations; zero callers. Beta r6_canon records graph_overview as destination (no def today, so no hint). Registry call _get_kind_expander().list_relations() survives.
# source:  tortoise/sdk.py @ 32011c4dad4b42364868cb0810ad8e7317a7dfb6
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'list_relations'
ARCHIVED_BATCH = 'b2-sdk-retirement-wave-1'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def list_relations(self) -> list[dict]:
        """List all relation declarations across installed packs.

        Returns [{"pack": ..., "predicate": ..., "fromKind": ..., "toKind": ...,
        "mechanism": ...}]. Pack relations describe valid edge types between
        entity kinds — use for schema discovery.
        """
        return _get_kind_expander().list_relations()
