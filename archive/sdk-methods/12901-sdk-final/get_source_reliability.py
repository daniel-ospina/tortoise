# Archived sdk-method `get_source_reliability` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_get_source_reliability`; recorded destination `manage_source_trust` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'get_source_reliability'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def get_source_reliability(self, url: str) -> dict:
        """Derive a Source's reliability (0-1) — query-time, cache-consistency-checked.

        Returns {"url", "reliability" (float 0-1 or None), "components",
        "cache": "fresh"|"recomputed"|"miss"}. The reliability value is the mean
        of the SAME modulated prior EP uses as base weight (single source of
        truth ``_compute_source_prior``), so ``reliability == inherited prior
        mean`` for single-source points (consistency invariant). Untiered +
        unassessed → None (reason 'untiered'). The cache
        (reliability/reliabilityComponents/reliability_derived_at) is a
        documented projection — recomputed when stale (interval elapsed or tier/
        sourceDate changed vs cached components) or after write events
        (set_source_tier/assess_source/create_source(tier=)).
        """
        url = self._resolve_source_url(url)
        import os  # noqa: I001
        from datetime import datetime, timezone
        from tortoise.source_credibility import resolve_tier
        proj = self._get_proj()
        now = datetime.now(timezone.utc)  # noqa: UP017
        interval = float(os.environ.get("TORTOISE_EP_REINHERIT_INTERVAL", "3600"))

        # Cache freshness check
        rows = proj.g.query(
            "MATCH (s:Source {url:$url}) "
            "RETURN s.reliability, s.reliabilityComponents, s.reliability_derived_at, "
            "s.credibilityTier, s.sourceKind, s.sourceDate, s.ingestedAt",
            params={"url": url},
        ).result_set
        cached = None
        if rows and rows[0][2]:
            cached_rel, cached_comp_raw, cached_at, c_tier, c_kind, c_sdate, c_ingested = rows[0]  # noqa: RUF059
            fresh = False
            try:
                derived = datetime.fromisoformat(str(cached_at).replace("Z", "+00:00"))
                if derived.tzinfo is None:
                    derived = derived.replace(tzinfo=timezone.utc)  # noqa: UP017
                fresh = (now - derived).total_seconds() < interval
            except (ValueError, TypeError):
                fresh = False
            if fresh:
                try:
                    import json as _json
                    cached_comp = _json.loads(cached_comp_raw) if cached_comp_raw else {}
                    # Inputs unchanged → serve cache (tier, sourceDate, ingestedAt
                    # are the derivation inputs; assessments clear the cache at write)
                    if (cached_comp.get("tier") == resolve_tier(c_tier, c_kind)
                            and cached_comp.get("sourceDate") == c_sdate
                            and cached_comp.get("ingestedAt") == rows[0][5]):
                        cached = (cached_rel, cached_comp)
                except (TypeError, ValueError, KeyError):
                    cached = None

        if cached is not None:
            rel, comp = cached
            return {"url": url, "reliability": rel, "components": comp, "cache": "fresh"}

        # Recompute from the single source of truth
        prior = self._compute_source_prior(url)
        if prior is None:
            # Untiered: assessment-only reliability (display — never feeds EP)
            arows = proj.g.query(
                "MATCH (p:Point {pointKind:'assessment'}) "
                "WHERE p.targetSource = $url AND (p.outdated IS NULL OR p.outdated = false) "
                "RETURN p.assessor, p.score, coalesce(p.assessorReputation, 0.5), p.createdAt "
                "ORDER BY p.createdAt",
                params={"url": url},
            ).result_set
            if arows:
                # Latest per (url, assessor) — mirrors _compute_source_prior dedup
                latest: dict[str, tuple[float, float]] = {}
                for assessor, score, rep, _created in arows:
                    try:
                        latest[assessor] = (float(rep), float(score))
                    except (TypeError, ValueError):
                        continue
                reps = [r for r, _s in latest.values()]
                rep_sum = sum(reps)
                weighted = (sum(r * s for r, s in latest.values()) / rep_sum
                            if rep_sum else 0.0)
                if weighted != weighted:  # NaN guard
                    weighted = 0.0
                comp = {"tier": None, "reason": "untiered; assessment-only",
                        "assessment_count": len(arows),
                        "assessment_weighted_mean": weighted}
                self._write_reliability_cache(url, weighted, comp, now)
                return {"url": url, "reliability": weighted, "components": comp,
                        "cache": "recomputed"}
            comp = {"tier": None, "reason": "untiered", "assessment_count": 0}
            self._write_reliability_cache(url, None, comp, now)
            return {"url": url, "reliability": None, "components": comp, "cache": "miss"}

        mean = prior["alpha"] / (prior["alpha"] + prior["beta"])
        comp = {
            "tier": prior["tier"],
            "sourceDate": prior["sourceDate"],
            "ingestedAt": prior["ingestedAt"],
            "factor": prior["factor"],
            "assessment_count": prior["assessment_count"],
            "decay": prior["decay"],
            "mean": mean,
        }
        self._write_reliability_cache(url, mean, comp, now)
        return {"url": url, "reliability": mean, "components": comp, "cache": "recomputed"}
