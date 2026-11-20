# Archived sdk-method `check_structure` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_check_structure`; recorded destination `graph_overview` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'check_structure'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def check_structure(self) -> list[dict]:
        """Check Gate 0→4 chain integrity. Thin delegation wrapper over the
        domain-validator registry (issue #405): runs the product-strategy
        graph-surface validator and maps its enriched output back to the
        legacy {type, id, message} contract (preserved for the mcp_server,
        tool_registry, test_sdk and test_integration_search consumers).

        Behavior note (#405): chain rules now run on LIVE points only (draft
        points are the orphaned_draft rule's job) — a draft with a dangling
        ref or no JTBD parent is no longer double-flagged."""
        proj = self._get_proj()
        from .domain_validators import (  # noqa: I001
            run_domain_graph_validators, to_legacy_shape,
        )
        violations, _drift = run_domain_graph_validators(
            "product-strategy", proj)
        return to_legacy_shape(violations)
