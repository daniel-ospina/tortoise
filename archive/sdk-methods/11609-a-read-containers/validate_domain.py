# Archived sdk-method `validate_domain` — REMOVED from the product surface (#11609).
#
# batch:   11609-a-read-containers
# reason:  retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container
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
ARCHIVED_SYMBOL = 'validate_domain'
ARCHIVED_BATCH = '11609-a-read-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def validate_domain(self, domain: str) -> dict:
        """Run a domain's graph-surface validators (issue #405) — advisory,
        read-only, on-demand. Returns
        ``{domain, ok, violations, drift}`` where violations are enriched,
        actionable dicts ({rule, kind, ref, message, fix}). Raises ValueError
        for an unknown domain (no loaded pack AND no registered validators)."""
        proj = self._get_proj()
        from .domain_validators import run_domain_graph_validators
        violations, drift = run_domain_graph_validators(domain, proj)
        return {
            "domain": domain,
            "ok": not violations,
            "violations": violations,
            "drift": drift,
        }
