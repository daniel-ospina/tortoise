# Archived sdk-method `compute_reputation` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: compute_reputation left the public SDK surface — privatised (body survives as `_compute_reputation`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
# source:  tortoise/sdk.py @ 424368c05d8ee9b26aa2d77e4c5a93a4b207b58f
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'compute_reputation'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def compute_reputation(self, subject_id: str) -> dict:
        """Derive reputation score for a Subject from event outcomes.

        Traverses: Subject -[:performs]-> Event -[:IMPL|NAND]-> Point
        Aggregates success/failure from direct event outcomes.
        Returns derived score (NOT stored).

        Returns {mean, total_events, impl_count, nand_count, alpha, beta, outcomes}.
        """
        proj = self._get_proj()
        # #3633 route-then-refuse (replaces #152's id-then-name fallback).
        # #152 kept Subject A (id='alice') from merging with Subject B
        # (name='alice') by preferring the id; the `else` arm still keyed
        # `s.name = $sid`, so TWO live same-name Subjects unioned BOTH carriers'
        # outcomes into one score. The resolver now returns exactly one live id
        # (id match still wins) and REFUSES on >=2 same-name carriers; a dead
        # coordinate resolves to None and yields the vacuous posterior.
        resolved_sid = resolve_entity_id_same_entity(
            proj.g, "Subject", subject_id)
        if resolved_sid is None:
            return {"mean": 0.5, "total_events": 0, "impl_count": 0,
                    "nand_count": 0, "alpha": 1.0, "beta": 1.0,
                    "outcomes": []}
        match_clause = "s.id = $sid"

        # Direct: Event connects directly to claim Points via IMPL/NAND.
        # ONTOLOGY.md §8: operators connect only epistemic targets
        # (:Point/:Subject/:Object/:Event). ⚠️ this reader is still
        # `:Point`-only — the #7853 read widening covers the EP/confidence
        # path, not reputation; widening it to the other three labels is
        # tracked by #7902 (the write path admits :Point/:Event only until
        # then, so a :Subject/:Object endpoint is spec-only here too).
        impl_rows = proj.g.query(
            "MATCH (s:Subject)-[:performs]->(e:Event) "
            "MATCH (e)-[:IMPL]->(p:Point) "
            "WHERE p.is_operator = false "
            "AND (p.outdated IS NULL OR p.outdated = false) "
            "AND e.eventKind <> 'humanApproval' "  # #531: no reputation from own approvals
            f"AND {match_clause} "
            # #11702: NO `coalesce(p.confidence, 0.5)` — an evidence point with
            # no persisted belief has no confidence to report, and the report is
            # the whole use of this column here (the Beta below counts IMPL vs
            # NAND, it does not weight by confidence). The fabricated 0.5 made
            # "no evidence recorded" read as "symmetric evidence".
            "RETURN p.id, p.content, p.confidence AS conf",
            params={"sid": resolved_sid},
        ).result_set
        nand_rows = proj.g.query(
            "MATCH (s:Subject)-[:performs]->(e:Event) "
            "MATCH (e)-[:NAND]->(p:Point) "
            "WHERE p.is_operator = false "
            "AND (p.outdated IS NULL OR p.outdated = false) "
            "AND e.eventKind <> 'humanApproval' "  # #531: no reputation from own approvals
            f"AND {match_clause} "
            # #11702: NO `coalesce(p.confidence, 0.5)` — an evidence point with
            # no persisted belief has no confidence to report, and the report is
            # the whole use of this column here (the Beta below counts IMPL vs
            # NAND, it does not weight by confidence). The fabricated 0.5 made
            # "no evidence recorded" read as "symmetric evidence".
            "RETURN p.id, p.content, p.confidence AS conf",
            params={"sid": resolved_sid},
        ).result_set

        # Collect outcomes
        outcomes: list[dict] = []
        # #11702 cycle 16: TOTAL coercion — a stored non-numeric mirror raised
        # `float()` here and took the whole reputation update with it, for a
        # report that does not weight by confidence in the first place.
        from .search_engine import belief_mirror as _belief_mirror

        for row in impl_rows:
            outcomes.append({"point_id": row[0], "content": row[1],
                             "confidence": _belief_mirror(row[2]),
                             "outcome": "IMPL"})
        for row in nand_rows:
            outcomes.append({"point_id": row[0], "content": row[1],
                             "confidence": _belief_mirror(row[2]),
                             "outcome": "NAND"})

        total = len(outcomes)
        impl_count = sum(1 for o in outcomes if o["outcome"] == "IMPL")
        nand_count = sum(1 for o in outcomes if o["outcome"] == "NAND")

        if total == 0:
            return {"mean": 0.5, "total_events": 0, "impl_count": 0, "nand_count": 0,
                    "alpha": 1.0, "beta": 1.0, "outcomes": []}

        # Simple Beta reputation: IMPL = success, NAND = failure
        # Prior: Beta(1, 1) uniform
        alpha = 1.0 + impl_count
        beta = 1.0 + nand_count
        mean = alpha / (alpha + beta)

        return {
            "mean": round(mean, 4),
            "total_events": total,
            "impl_count": impl_count,
            "nand_count": nand_count,
            "alpha": alpha,
            "beta": beta,
            "outcomes": outcomes[:20],  # cap for readability
        }
