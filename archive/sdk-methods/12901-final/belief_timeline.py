# Archived sdk-method `belief_timeline` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: belief_timeline left the public SDK surface — privatised (body survives as `_belief_timeline`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'belief_timeline'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def belief_timeline(self, topic: str, limit: int = 50) -> list[dict]:
        """Dated, ordered belief chain for a topic (plan §6.1, J-4, DE2E-6).

        Returns decision Points aboutObject-connected to the topic entity,
        ordered by validFrom ascending, each shaped as
        {content, pointKind, validFrom, status, linked_by, related} where
        linked_by is the temporal edge (NAND via an operator, or CORRECTS via
        supersede_point) to the NEXT point in the chain, and related holds
        the linked point ids.

        An undated point (absent ``validFrom`` ⇒ open/unbounded start, §4.7)
        sorts FIRST — before every dated entry — never as the newest belief
        (#3654).

        ``limit`` is a NEWEST-END window: it returns at most the newest
        ``limit`` entries of the chain — the current belief is the last of
        them — never the oldest (#7293).

        ``topic`` is an id or a NAME coordinate, resolved through
        ``entity_identity.resolve_entity_id`` (#5509). A name is a mutable
        natural key: under #3590 D2 two live Objects may hold it, so the read
        must resolve to EXACTLY ONE live Object and never union both carriers'
        decision Points into one chain (measured: renaming an unrelated
        company to a topic's name appended its decisions to that topic's
        timeline). Two or more live carriers REFUSES
        (:class:`AmbiguousEntityName`, the non-folded entry already recorded by
        the resolver); no live holder (absent, or held only by terminal
        carriers — D2's ``single_terminal_holder_reference``) yields ``[]`` and
        records the refusal, so it is distinguishable from a union.

        ``canonical_name`` is matched by the resolver's name arm, not by this
        read: it is a second spelling of the same natural key, so it takes the
        same holder-count discipline instead of widening the union.

        A single live holder that carries NO ``id`` (a raw/legacy producer
        wrote it — ``hosted_api``'s capture wiring mints such a stub before its
        entity step adopts it) is NOT refused here: the identity question is
        already answered (exactly one live candidate) and the chain is
        traversed through the ``aboutObject`` edges, so only the id-keyed
        ADDRESS is missing, not the answer. That one case reads the holder by
        its (unique, live) name — it cannot fold two carriers, because the
        resolver just proved there is only one.
        """
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        proj = self._get_proj()
        # ROUTE-THEN-REFUSE (#5509, plan §B.1). The old read was
        # `... -[:aboutObject]->(o:Object) WHERE o.name = $topic OR
        # o.canonical_name = $topic`, i.e. a NAME-keyed match over every
        # carrier. Two live same-name Objects are legal, so that read folded
        # two identities' beliefs into one chain — and a name held only by a
        # TERMINAL Object still resolved (D2 refuses that). Resolve the
        # coordinate to one live id and match the id: the identity decision
        # lives in the resolver, not here.
        try:
            entity_id = resolve_entity_id_same_entity(proj.g, "Object", topic)
            unaddressable = False
        except UnaddressableEntityName:
            # EXACTLY ONE live holder, and it carries no `id` property. The
            # resolver refused it because an ID-KEYED WRITE could not address
            # it; this read only needs to reach it, and the holder set is
            # already proven unique — so the name below is unambiguous. The
            # live predicate is what keeps the read from touching a terminal
            # carrier, and it is the ONLY name-keyed branch left here.
            entity_id = None
            unaddressable = True
        if entity_id is None and not unaddressable:
            # No live holder: an absent name, or one held ONLY by terminal
            # carriers (refused and recorded by the resolver above — D2 never
            # hands a caller a terminal id). Nothing live to build a chain on.
            return []
        if unaddressable:
            # #12120 review: the resolver above admitted this holder over the
            # WRITER's widened union, so re-spelling the predicate here as raw
            # `name`/`canonical_name` asked a NARROWER key space than the read it
            # justifies — an id-less stub matched only by the folded spelling or
            # an `alias` resolved, then matched NOTHING, and `belief_timeline`
            # reported "no decisions" about a topic it had just resolved. The
            # uniqueness excuse in the census was a different question: unique is
            # not the same as spelled-this-way.
            _space, _space_params = name_key_space_same_entity("o", topic)
            if not _space:  # pragma: no cover - the resolver refuses a blank
                return []
            entity_predicate = (
                f"({_space}) AND {_terminal_excluded('o.status')}")
        else:
            entity_predicate = "o.id = $oid"
            _space_params = {}
        # The DB window MUST be taken from the NEWEST end (#7293). The former
        # `ORDER BY p.validFrom LIMIT` was ASCENDING, so it truncated to the
        # OLDEST rows and the `entries[-limit:]` tail below could only re-take
        # the tail of an already-oldest set — `limit=1` returned the earliest
        # belief, not the current one.
        #
        # FalkorDB orders NULL LAST ascending and FIRST descending (measured
        # on four points, #7293): a bare `validFrom DESC` would pull an undated
        # row — the OLDEST belief per §4.7 — into a small window and could
        # evict the genuinely newest dated point. `(p.validFrom IS NULL)` sorts
        # `false` (dated) before `true` (undated), so this DESC window is
        # newest-dated first, undated last, matching the Python global sort.
        rows = proj.g.query(
            "MATCH (p:Point {pointKind:'decision'})-[:aboutObject]->"
            "(o:Object) "
            "WHERE (p.is_operator IS NULL OR p.is_operator = false) "
            f"AND {entity_predicate} "
            "RETURN p.id, p.content, p.validFrom, p.status, p.outdated "
            "ORDER BY (p.validFrom IS NULL), p.validFrom DESC LIMIT $limit",
            params={"oid": entity_id, "limit": limit, **_space_params},
        ).result_set
        entries = [list(r) for r in rows]
        # Superseded priors dropped out of the topic query (supersede_point
        # transferred their aboutObject edges) — re-attach them via the
        # CORRECTS chain so the timeline keeps the outdated belief (#1080).
        if entries:
            topic_ids = [r[0] for r in entries]
            old_rows = proj.g.query(
                "MATCH (cur:Point)-[:CORRECTS]->(old:Point {pointKind:'decision'}) "
                "WHERE cur.id IN $ids "
                "RETURN old.id, old.content, old.validFrom, old.status, "
                "       old.outdated ORDER BY old.validFrom",
                params={"ids": topic_ids},
            ).result_set
            known = {r[0] for r in entries}
            for r in old_rows:
                if r[0] not in known:
                    entries.append(list(r))
            # Globally ordered by validFrom (superseded priors appended).
            # `not is None` puts an absent validFrom (open start, §4.7) FIRST
            # — undated is the oldest, never the newest (#3654): the old
            # `is None` key sorted them last, and the `[-limit:]` tail then
            # presented an undated point as the current belief.
            entries.sort(key=lambda e: (e[2] is not None, e[2] or ""))
        out = []
        ids = [e[0] for e in entries]
        for pid, content, vf, status, outdated in entries[-limit:]:
            # Temporal link to the NEXT point in the chain.
            linked_by = None
            related = []
            for other_id in ids:
                if other_id == pid:
                    continue
                nand = proj.g.query(
                    "MATCH (op:Point {is_operator:true})-[:NAND]->"
                    "(:Point {id:$a}), "
                    "(op)-[:NAND]->(:Point {id:$b}) RETURN count(op)",
                    params={"a": pid, "b": other_id},
                ).result_set
                if nand and nand[0][0] > 0:
                    linked_by = "NAND"
                    related.append(other_id)
                    break
                corr = proj.g.query(
                    "MATCH (:Point {id:$a})-[:CORRECTS]->(:Point {id:$b}) "
                    "RETURN count(*)",
                    params={"a": pid, "b": other_id},
                ).result_set
                if corr and corr[0][0] > 0:
                    linked_by = "CORRECTS"
                    related.append(other_id)
                    break
            entry = {
                "content": content,
                "pointKind": "decision",
                "validFrom": vf,
                "status": status,
                "linked_by": linked_by,
                "related": related,
            }
            if outdated:
                entry["outdated"] = True
            out.append(entry)
        return out
