# Archived sdk-method `create_document` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_create_document`; recorded destination `create_entity` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'create_document'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def create_document(self, title: str, documentKind: str, **props) -> dict:
        """Thin alias for create_entity(type='document') — epic #888 W2."""
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        # #8171 (review P2): this door routes `extractedFrom` to the SAME
        # `_link_source` as `create_entity`, so it needs the SAME pre-write
        # shape check. Without it the choke point's container gate turns a bad
        # ref into a SILENT no-op — the caller gets a 200, no provenance, and
        # no advisory. Pre-fix, only a NON-ITERABLE scalar failed loudly (and it
        # failed AFTER the CREATE, leaving an orphan); the two other bad shapes
        # were silent — a DICT had its keys minted as `:Source` nodes (invented
        # provenance), and a mixed list DROPPED its non-str members. Those two
        # silent cases are what this guard is for on this door: a ref the door
        # cannot honour must not be accepted in silence.
        _validate_extracted_from(props, door="create_document")

        did = self.ulid()
        result = self._create_entity("Document", did, {"title": title, "documentKind": documentKind, **props}, "DocumentCreated")
        # #394: provenance parity with create_point — link the document Source
        # → Source via extractedFrom (Ontology v3.3) when the caller passes a
        # source ref. D10: the entity label is Source (a document is a :Source).
        if props.get("extractedFrom"):
            # #7405: parity with `create_entity`'s document leg — same
            # MERGE-idempotent writer, same bounded retry.
            self._graph_write_with_retry(
                lambda: self._get_proj()._link_source(
                    did, props["extractedFrom"], label="Source"),
                what="create_document extractedFrom edges",
            )
        # #8171 (review P2): the ADVISORY must be delivered on this door too.
        # This is the documented alias AND a live MCP tool
        # (`tortoise_create_document`), so a caller using it would otherwise
        # get no advisory while the same write through
        # `create_entity(type='document')` does — the two doors disagreeing is
        # the defect the shared guard exists to prevent, and an advisory that
        # reaches only one of two doors cannot change behaviour.
        result.update(self._provenance_advisory(result, props,
                                                entity_type="document"))
        return result
