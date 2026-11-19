# Archived sdk-method `get_events` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_get_events`; recorded destination `get_entity` (#12901 final batch, #4282 Phase 2).
# source:  tortoise/sdk.py @ 1231060fd2be9205b9b3bba1fd315e9b19b3602e
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'get_events'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def get_events(self, eventKind: str | None = None, limit: int = 20) -> list[dict]:
        """Get recent Events, optionally filtered by eventKind."""
        proj = self._get_proj()
        if eventKind:
            out = [r[0] for r in proj.g.query(
                "MATCH (e:Event {eventKind: $ek}) RETURN properties(e) ORDER BY e.startedAt DESC LIMIT $lim",
                params={"ek": eventKind, "lim": limit}
            ).result_set]
            _ledger_record("Event", out)  # #7983: a served page (the other branch too)
            return out
        out = [r[0] for r in proj.g.query(
            "MATCH (e:Event) RETURN properties(e) ORDER BY e.startedAt DESC LIMIT $lim",
            params={"lim": limit}
        ).result_set]
        _ledger_record("Event", out)  # #7983: a served page, not a pool
        return out
