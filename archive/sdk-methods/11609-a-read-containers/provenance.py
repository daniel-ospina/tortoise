# Archived sdk-method `provenance` — REMOVED from the product surface (#11609).
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
ARCHIVED_SYMBOL = 'provenance'
ARCHIVED_BATCH = '11609-a-read-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def provenance(self, point_id: str) -> dict:
        """Provenance chain — "Who decided this?" Point → Subject → delegation."""
        point = self.get_point(point_id)
        if not point:
            return {"error": f"Point {point_id} not found"}
        author = point.get("authoredBy", "")
        chain = {"point": {"id": point_id, "content": (point.get("content") or "")[:200],
                           "authoredBy": author}}
        # #3998 (D30): the raw is OPTIONAL. Attach the source's index entry —
        # including the absent-raw state — so a memory whose raw was deleted is
        # still readable AND SAYABLE. `offline`/`access_revoked` are conditions
        # observed live, never stored (Option A ruling, 2026-10-14), so this
        # reader reports the RECORD it holds; a caller that has actually probed
        # the raw routes the observation through
        # `raw_state.resolve_raw_availability`.
        # Additive: emitted only when the point HAS an extractedFrom source,
        # so a sourceless point's response is byte-identical. Set BEFORE the
        # early returns below, so every exit path carries it.
        _raw = self._raw_entry_for_point(point_id)
        if _raw is not None:
            chain["raw"] = _raw
        if not author:
            return chain
        proj = self._get_proj()
        # #3633 route-then-refuse: `authoredBy` is written as a resolved
        # Subject ID by `file_human_approval`, but older Points (and direct
        # `create_point(authoredBy=<name>)` callers) carry a NAME, and the
        # historical match was case-insensitive. The old
        # `toLower(s.name) = toLower($n)` probe read by NAME ONLY — so it
        # returned None for an id-valued author — AND took `rows[0]` with no
        # live filter, a silent pick over same-name carriers. Resolve id-first
        # then one live case-insensitive name (>=2 -> AmbiguousEntityName).
        resolved = resolve_entity_id_same_entity(proj.g, "Subject", author,
                                                case_insensitive_name=True)
        if resolved is None:
            return {**chain, "subject": None}
        rows = proj.g.query(
            "MATCH (s:Subject {id:$sid}) RETURN properties(s)",
            params={"sid": resolved},
        ).result_set
        if not rows:
            return {**chain, "subject": None}
        sub = rows[0][0]
        chain["subject"] = {"id": sub.get("id"), "name": sub.get("name"),
                             "kind": sub.get("subjectKind", ""),
                             # #1423: the deciding subject's own lifecycle status
                             # rides the chain, so "who decided this?" can also
                             # report that the role/team has since been
                             # superseded. Never absent — additive for existing
                             # consumers — and DERIVED-OR-ABSENT since #9974 R1: a
                             # status-less birth stores no `status`, which READS
                             # live (`live._terminal_excluded`), so it renders as
                             # `'live'` here rather than as `''`. This lookup is
                             # `:Subject`-anchored, so the label is fixed by
                             # construction and the `:Event`-shaped `""` of
                             # `expand_relationships` does not apply (#9974 R1
                             # review P2).
                             "status": status_as_reported(sub.get("status"))}
        # ponytail: follow outgoing rels for Role → Org delegation
        rels = proj.g.query(
            "MATCH (s:Subject {id:$sid})-[r]->(n) RETURN type(r), labels(n)[0], properties(n)",
            params={"sid": sub["id"]},
        ).result_set
        chain["delegation"] = [{"via": r[0], "node_type": r[1], "props": r[2]} for r in rels]
        return chain
