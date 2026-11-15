# Archived sdk-method `calibration_passed` — REMOVED from the product surface (#11609).
#
# batch:   b2-sdk-retirement-wave-1
# reason:  Gate B calibration-milestone reader; zero SDK callers (check_gates takes the flag as a parameter). Beta r3 records check_confidence. _CALIBRATION_MARKER_KEY stays: projection config-reset reads it.
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
ARCHIVED_SYMBOL = 'calibration_passed'
ARCHIVED_BATCH = 'b2-sdk-retirement-wave-1'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def calibration_passed(self) -> bool:
        """True once the Gate B calibration milestone marker is stored.

        Local SDK contract (DE2E-7, epic-264 §6.3): no GitHub dependency —
        the workflow-layer ``check_gates`` helper composes this with #320's
        state. #787 re-uses this reader; it does NOT re-implement it.
        """
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (m:Meta {key:$key}) RETURN m.recordedAt",
            params={"key": self._CALIBRATION_MARKER_KEY},
        ).result_set
        return bool(rows)
