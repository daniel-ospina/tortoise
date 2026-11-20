# Archived sdk-method `topic_summarize` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_topic_summarize`; recorded destination `search_knowledge` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'topic_summarize'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def topic_summarize(
        self,
        topic: str,
        *,
        max_seeds: int = 50,
        max_hops: int = 1,
        include_relationships: bool = True,
    ) -> dict:
        """Epistemic topic summarization — settled vs contested structure (#592).

        For a topic query, returns the epistemic structure: what is significant
        (settled — high confidence, strong connections) and what is contested
        (elevated variance, NAND conflicts), plus the argument topology.

        Args:
            topic: Topic string (e.g. "pricing", "architecture").
            max_seeds: Max seed Points to retrieve from about* edges + content match.
            max_hops: Operator-chain expansion depth (0 = seeds only, 1 = neighbors).
            include_relationships: Whether to include argument topology.

        Returns:
            dict with keys: topic, total_points, significant, contested,
            disputed_pairs, argument_structure, meta.
        """
        from .topic_summarization import topic_summarize as _summarize
        result = _summarize(
            self._get_proj().g,
            topic,
            max_seeds=max_seeds,
            max_hops=max_hops,
            include_relationships=include_relationships,
        )
        return result.to_dict()
