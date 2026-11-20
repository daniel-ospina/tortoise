# Archived sdk-method `audit` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_audit__svc`; recorded destination `graph_overview` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'audit'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def audit(self, point_kinds: list[str] | None = None) -> dict:
        """Audit graph wiring quality — structured JSON report (epic #348).

        Runs the audit checks (missing sourceKind point-level legacy + Source-
        level canonical, missing sourceDate, superseded points without a
        CORRECTS edge, live IMPL/NAND edges into superseded points, live
        IMPL/NAND edges into superseded OBJECTS (#11826 — that lane moves no
        edges, so a survivor there is lost belief, not leftover wiring), a
        superseded Object whose ``supersededBy`` name resolves to no node,
        naive-IMPL heuristic, low-confidence operators without mitigation, legacy
        ``mitigates`` edges, and inverted validity windows — a persisted
        ``validTo`` earlier than ``validFrom``, which no instant satisfies).
        Returns per-check counts (uncapped) + capped
        samples + summary + exit_code (0 clean, 1 issues found — the
        check-consistency precedent).

        point_kinds: Optional list of pointKind values to scope the audit
                     (default: all Points). ⛔ The scope covers the POINT-anchored
                     checks only: the two entity-anchored ones
                     (``superseded_object_active_edges``, ``superseded_by_unresolvable``)
                     read `:Object` nodes, and `pointKind` is a Point-only property,
                     so scoping them would false-CLEAN every Object — they are
                     global by construction (#11826).
        """
        from tortoise.audit import audit_graph
        proj = self._get_proj()
        return audit_graph(proj, point_kinds=point_kinds).to_dict()
