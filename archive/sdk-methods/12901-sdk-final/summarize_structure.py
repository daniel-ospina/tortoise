# Archived sdk-method `summarize_structure` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_summarize_structure`; recorded destination `graph_overview` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'summarize_structure'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def summarize_structure(self) -> dict:
        """Structure summary — counts over the WHOLE graph (#2205), not just
        the product-strategy gate kinds.

        Pre-#2205 this tallied the five gate kinds only, so on evidence-heavy
        graphs (statements/observations/hypotheses, decisions, events, pack
        kinds, ...) the returned 'total' read 0 or near-0 while the graph was
        full — 'Demo graph created — N points' and per-kind stats lied on real
        graphs (self-host and hosted). Now returns flat {key: int} counts:

          total      — non-operator Points across EVERY pointKind present
                       ('N points' — the real graph size). Operators are
                       reported separately (Tortoise surfaces treat operators
                       as their own category, never as 'points').
          operators  — operator Points ('M operators'): modern is_operator
                       nodes AND legacy op_type-only bridges (the codebase's
                       canonical operator predicate, #943 parity).
          gate0_jtbds … gate4_requirements — the product-strategy gate-kind
                       counts (keys unchanged; a SUBSET of total).
          gate_total — the gate-kind subtotal (the pre-#2205 meaning of
                       'total', kept under an honest label).

        The non-operator predicate is absence-or-false PLUS op_type IS NULL:
        plain Points may carry no is_operator property at all (only operators
        set it) and legacy operators carry op_type without it — a bare
        `n.is_operator = false` drops the first from every count and leaks the
        second into 'total'. list_pointkinds() uses the SAME predicate, so the
        per-kind stats add up to 'total' whenever every Point carries a
        pointKind (untyped legacy Points are counted by total only).

        P1 #49: gate keys are pointKind-based (jobToBeDone, useCase,
        userJourney, workflow, requirement) after the context-string re-key.
        """
        proj = self._get_proj()
        gates = [
            ("gate0_jtbds", "jobToBeDone"),
            ("gate1_use_cases", "useCase"),
            ("gate2_user_journeys", "userJourney"),
            ("gate3_workflows", "workflow"),
            ("gate4_requirements", "requirement"),
        ]
        # Canonical non-operator / operator predicates (parity with ep.py
        # contested-claim scan and the #943 legacy operator detection).
        not_operator = ("(n.is_operator IS NULL OR n.is_operator = false) "
                        "AND n.op_type IS NULL")
        is_operator = "(n.is_operator = true OR n.op_type IS NOT NULL)"
        result: dict[str, int] = {}
        for key, kind in gates:
            result[key] = proj.g.query(
                "MATCH (n:Point {pointKind:$k}) "
                f"WHERE {not_operator} "
                "RETURN count(n)",
                params={"k": kind},
            ).result_set[0][0]
        result["total"] = proj.g.query(
            "MATCH (n:Point) "
            f"WHERE {not_operator} "
            "RETURN count(n)",
        ).result_set[0][0]
        result["operators"] = proj.g.query(
            "MATCH (n:Point) "
            f"WHERE {is_operator} "
            "RETURN count(n)",
        ).result_set[0][0]
        result["gate_total"] = sum(result[key] for key, _ in gates)
        return result
