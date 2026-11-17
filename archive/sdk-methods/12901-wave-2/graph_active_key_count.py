# Archived sdk-method `graph_active_key_count` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  Non-revoked key count per graph. Body survives PRIVATE as `_graph_active_key_count`; recorded destination `list_keys`.
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
ARCHIVED_SYMBOL = 'graph_active_key_count'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def graph_active_key_count(self, org_id: str, graph_id: str) -> int:
        """ACTIVE (non-revoked) APIKey nodes bound to a graph — the
        key_count source for GET /v1/graphs (parity with the Supabase
        count_graph_keys seam; C2 P2: graph_key_ids is the cascade source
        and must NOT be reused for the meter — after C3's standalone
        revoke, counting all keys would overcount vs Supabase).

        #2306: only ever call this with a CUSTOM graph's id. The default
        graph is not key-bindable (no per-graph keys exist — supabase
        enforces this structurally, and _ensure_graph_exists 404s
        default-kind mints) — the list seam short-circuits kind='default'
        rows to 0 BEFORE this meter, so legacy bound-default APIKey nodes
        (pre-guard mints / raw control-plane writes, graph_id = the
        default node's real gid) never resurface as a Graphs-tab count;
        they stay listable + revocable via the unfiltered key list."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {org_id:$tid, graph_id:$gid}) "
            "WHERE k.revoked_at IS NULL RETURN count(k)",
            params={"tid": org_id, "gid": graph_id},
        ).result_set
        return int(rows[0][0]) if rows else 0
