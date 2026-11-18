# Archived sdk-method `delete` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: delete left the public SDK surface — privatised (body survives as `_delete`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'delete'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def delete(self, id: str) -> bool:
        """One delete for a Point OR an entity (epic #888 W2).

        An operator IS a Point (``is_operator=true``), so
        ``delete(<operator_id>)`` removes an operator edge — operators are not
        delete-less, and a mislabelled or superseded edge is not permanent
        (#6131). The verb is deliberately generic, so its name says Point/entity
        rather than naming operators.

        Destructive. Detects the node type by label:
          - Point → delete_point (tag GC + `PointRetracted` :GraphEvent)
          - Entity → delete_entity
        Returns True if a node was found and deleted, False otherwise.

        Can raise instead of returning: the Point branch inherits both of
        `delete_point`'s refusals — `WriteNotLandedError`
        (``operation="delete_point"``) when the destruction record does not
        confirm after a landed destroy (``destroy_verified=True``), and the same
        class with ``destroy_verified=False`` when NEITHER store's state is known.
        The entity branch is unchanged.

        #3300: for the Point branch the `PointRetracted` row is the
        :GraphEvent SUBSCRIBER signal only — rebuild parity comes from the
        JSONL `EntityMutated op="delete"` record, because the `PointRetracted`
        fold tombstones and a hard delete must replay as a hard delete.

        #4649: resolution includes ``by_url``, matching ``get_entity`` — a
        url-keyed ``:Source`` used to return ``False`` here (the node was never
        found) while the read path resolved it.
        """
        resolved = self._get_proj()._resolve_entity(
            id, by_id=True, by_eventId=True, by_url=True)
        if not resolved:
            return False
        if resolved[0]["label"] == "Point":
            return self.delete_point(id)
        return self.delete_entity(id)
