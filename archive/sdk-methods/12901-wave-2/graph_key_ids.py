# Archived sdk-method `graph_key_ids` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  APIKey ids bound to a graph (delete cascade). Body survives PRIVATE as `_graph_key_ids`; recorded destination `list_keys`.
# source:  tortoise/sdk.py @ d741bd4a5a8d45198ec9c8fd2324c93ca746af5e
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'graph_key_ids'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def graph_key_ids(self, org_id: str, graph_id: str) -> list[str]:
        """APIKey node ids bound to a graph — the delete-cascade source
        (every key dies with the graph, E2E-8). Revoked or not — the
        cascade must revoke rows that are somehow still active AND clean
        up revoked ones (idempotent)."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {org_id:$tid, graph_id:$gid}) RETURN k.id",
            params={"tid": org_id, "gid": graph_id},
        ).result_set
        return [r[0] for r in rows]
