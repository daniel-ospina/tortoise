# Archived sdk-method `dream_health_state` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_dream_health_state`; recorded destination `graph_overview` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'dream_health_state'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def dream_health_state(self) -> dict:
        """C5-emitted region_attempts record (consumed by 903-C7's health
        surface): per-root attempt count + backoff state + stale_unresolved.
        """
        # P2-review (#1243): timestamp fields are _monotonic() BASE
        # (process-local seconds, meaningless across restart/process) —
        # C7's hosted health surface must not present them as wall-clock.
        dreamer = self._get_dreamer()
        return {
            "clock": "monotonic",
            "retry_attempts": dict(self._retry_attempts),
            "retry_backoff_until": dict(self._retry_backoff_until),
            "stale_unresolved": dict(self._stale_unresolved),
            # Epic 903-C4 (#1242): DE2E-6b cost metric — censored-update
            # count from the last dream pass (C7 surfaces it; never gated).
            "warm_skipped_updates": getattr(
                dreamer, "_last_warm_skipped", 0) or 0,
        }
