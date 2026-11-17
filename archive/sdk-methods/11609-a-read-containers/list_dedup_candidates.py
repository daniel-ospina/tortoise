# Archived sdk-method `list_dedup_candidates` — REMOVED from the product surface (#11609).
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
ARCHIVED_SYMBOL = 'list_dedup_candidates'
ARCHIVED_BATCH = '11609-a-read-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def list_dedup_candidates(self, candidate_type: str = "content",
                              limit: int = 50) -> list[dict]:
        """Review queue for dedup candidates (plan §6.1, DE2E-3/DE2E-2).

        candidate_type='content': pending (unreviewed) content candidates —
        draft decision Points flagged by the two-tier dedup, shaped as
        {id, content, pointKind, method, similarity, target_id, batch_id}.
        candidate_type='entity': ambiguity pairs over live ``:Object``/``:Subject``
        — the entity resolver chain's review band (``entity_resolver``), shaped as
        {id, name, existing_id, existing_name, label, kind, method, similarity,
        candidate_type, status, auto_mergeable}.

        ⛔ **An empty entity queue is a MEASUREMENT, never a stub** (#12047). The
        entity scan runs on every call; if it cannot run it RAISES
        ``EntityScanUnavailable`` instead of returning ``[]``. The stub this
        replaced answered ``[]`` unconditionally, so a caller had to read *"could
        not answer"* as *"no duplicate entities"* — the fail-open shape
        ``record-reconcile``'s exit 2 exists to prevent.
        """
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        if candidate_type not in ("content", "temporal", "entity"):
            raise ValueError(
                f"candidate_type must be 'content', 'temporal' or 'entity', "
                f"got {candidate_type!r}")
        proj = self._get_proj()
        if candidate_type == "entity":
            # #12047: real pairs or a loud failure. Never the ``[]`` stub.
            from .entity_resolver import entity_candidates
            # A reviewed PAIR is already suppressed inside the scan
            # (`entity_resolver._pair_reviewed`), so what comes back is the
            # pending queue — the same contract as the content branch, without a
            # second filter that would silently disagree with the first.
            return entity_candidates(proj.g, limit=limit)
        if candidate_type == "content":
            rows = proj.g.query(
                "MATCH (n:Point) "
                "WHERE n.dedup_candidate = true AND n.dedup_reviewed IS NULL "
                "AND n.status = 'draft' "
                "AND (n.is_operator IS NULL OR n.is_operator = false) "
                "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $limit",
                params={"limit": limit},
            ).result_set
            out = []
            for (props,) in rows:
                out.append({
                    "id": props.get("id"),
                    "content": props.get("content"),
                    "pointKind": props.get("pointKind"),
                    "method": props.get("dedup_method"),
                    "similarity": props.get("dedup_similarity"),
                    "target_id": props.get("dedup_target_id"),
                    "existing_id": props.get("dedup_target_id"),  # §6.1 alias
                    "candidate_type": "content",
                    "status": props.get("dedup_reviewed") or "pending",
                    "batch_id": props.get("batch_id"),
                })
            return out
        # Temporal candidates (W-4, #786): contradictory/replacement decision
        # Points whose prior was LIVE at extraction — wire at promotion.
        rows = proj.g.query(
            "MATCH (n:Point) "
            "WHERE n.temporal_candidate = true AND n.temporal_reviewed IS NULL "
            "AND n.status = 'draft' "
            "AND (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $limit",
            params={"limit": limit},
        ).result_set
        out = []
        for (props,) in rows:
            out.append({
                "id": props.get("id"),
                "content": props.get("content"),
                "pointKind": props.get("pointKind"),
                "target_id": props.get("temporal_target_id"),
                "existing_id": props.get("temporal_target_id"),
                "replacement": bool(props.get("temporal_replacement")),
                "candidate_type": "temporal",
                "status": props.get("temporal_reviewed") or "pending",
                "batch_id": props.get("batch_id"),
            })
        return out
