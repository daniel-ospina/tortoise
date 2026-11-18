# Archived sdk-method `session_context` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: session_context left the public SDK surface — privatised (body survives as `_session_context`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'session_context'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def session_context(self) -> dict:
        """Return 'what happened last session' — diary entries, Points, Events, confidence changes.
        Returns structured dict with explicit 'no_prior_sessions' when graph is empty.

        #2207: recent_points / confidence_changes are digest surfaces — rule/config
        noise and markdown fragments ('---', '*Gate: filed as child issue…', table
        rows, 'Label: value' lines) are excluded so the session-start digest lists
        actual decisions/claims only.
        """
        proj = self._get_proj()
        diary_entries = [r[0] for r in proj.g.query(
            "MATCH (n:Point {pointKind:'diary'}) "
            "WHERE n.is_operator = false "
            "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT 10"
        ).result_set]
        recent_points = [r[0] for r in proj.g.query(
            "MATCH (n:Point) "
            "WHERE n.is_operator = false "
            "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT 20"
        ).result_set]
        recent_events = [r[0] for r in proj.g.query(
            "MATCH (e:Event) RETURN properties(e) ORDER BY e.startedAt DESC LIMIT 20"
        ).result_set]
        # #11702 cycle 24: the digest reports a belief, so the mirror is read
        # through the band (`belief_mirror`) like every other surface — a legacy
        # `"0x1p4"` / `2.5` mirror was served here as a confidence while
        # `annotate`, `why` and all three rankers called the claim absent, and a
        # raw string in a digest is a serialisation hazard besides.
        from .search_engine import belief_mirror as _belief_mirror
        confidence_changes = [
            {"id": r[0], "content": r[1], "pointKind": r[2],
             "confidence": _belief_mirror(r[3]), "updatedAt": r[4]}
            for r in proj.g.query(
                "MATCH (n:Point) WHERE n.confidence IS NOT NULL "
                "AND n.is_operator = false "
                "RETURN n.id, n.content, n.pointKind, n.confidence, n.updatedAt "
                "ORDER BY n.updatedAt DESC LIMIT 20"
            ).result_set
        ]
        # #2207: rule/config noise must not reach the digest as 'recent decisions'.
        recent_points = [p for p in recent_points
                         if not _is_digest_noise(p.get("content"))]
        confidence_changes = [c for c in confidence_changes
                              if not _is_digest_noise(c.get("content"))]
        no_prior = not diary_entries and not recent_points and not recent_events
        return {
            "no_prior_sessions": no_prior,
            "diary_entries": diary_entries,
            "recent_points": recent_points,
            "recent_events": recent_events,
            "confidence_changes": confidence_changes,
        }
