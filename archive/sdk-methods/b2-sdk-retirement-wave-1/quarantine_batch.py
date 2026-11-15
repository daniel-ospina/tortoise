# Archived sdk-method `quarantine_batch` — REMOVED from the product surface (#11609).
#
# batch:   b2-sdk-retirement-wave-1
# reason:  Thin SDK delegate to tortoise.mining.quarantine_batch (which stays). No PRODUCTION caller: the only call sites were tests, re-pointed at tortoise.mining.quarantine_batch, which is exactly what the delegate called. Beta w11_lifecycle records update_knowledge as destination.
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
ARCHIVED_SYMBOL = 'quarantine_batch'
ARCHIVED_BATCH = 'b2-sdk-retirement-wave-1'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def quarantine_batch(self, batch_id: str, *, reason: str) -> dict:
        """Quarantine a batch (W-3 fail path) — blocks promote_point on its
        Points until a re-run passes (plan §6.1 pinned SDK signature).

        Thin delegate to tortoise.mining.quarantine_batch — batch lifecycle
        state lives on :Batch marker nodes (operational metadata).
        """
        from .mining import quarantine_batch as _qb
        return _qb(self._get_proj(), batch_id, reason=reason)
