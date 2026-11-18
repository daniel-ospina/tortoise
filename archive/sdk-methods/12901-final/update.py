# Archived sdk-method `update` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: update left the public SDK surface — privatised (body survives as `_update`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'update'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def update(self, id: str, **props) -> dict:
        """One update for a Point OR an entity (epic #888 W2).

        A graph operator IS a Point (``is_operator=true``), so it takes the
        Point arm below: ``update(<operator_id>, label=...)`` edits an existing
        operator edge in place. So ``update`` belongs to the operator-mutation
        vocabulary even though the deleted ``operator_action`` exposed only
        mitigate/annotate (#6131; #4282 deleted that door — ``mitigate_operator`` and
        ``annotate_operator`` are the surviving writes).

        Note what this path does NOT do: unlike ``create_operator``, which returns
        a structured ``undeclared_relation`` warning when the predicate is neither
        a core operator label (``pack_registry.CORE_OPERATOR_LABELS`` — ONTOLOGY
        §3.9's ``IMPL``/``NAND`` plus §8's semantics vocabulary) nor a predicate an
        installed pack declares, ``update(<operator_id>, label=...)`` performs no
        declaredness check and installs the label silently. Take valid predicates
        from ``pack_registry.list_relations()`` for the pack-declared leg, or the core
        set above. (#12901 wave 1 retired the SDK's ``list_relations()`` door; the
        registry call it wrapped is unchanged.)

        Detects the node type by label:
          - Point → point-lifecycle semantics (delegates to update_point):
            draft→live promote via status (the only transition allowed; an
            already-live point is an idempotent no-op), version increment for
            :Point:Object nodes, status validation against
            POINT_STATUS_VALUES, context rejected.
          - Entity (Subject/Object/Event/Document/Source) → plain property
            update (delegates to update_entity).
          - Unknown id → returns {} (no write) — legacy-compatible.

        #4649: resolution includes ``by_url`` so a url-keyed ``:Source`` (the
        shape ``get_entity`` has always addressed, and ``_link_source`` mints)
        routes to ``update_entity`` instead of silently returning ``{}``.
        """
        resolved = self._get_proj()._resolve_entity(
            id, by_id=True, by_eventId=True, by_url=True)
        if not resolved:
            return {}
        if resolved[0]["label"] == "Point":
            return self.update_point(id, **props)
        return self._update_entity_any(id, **props)
