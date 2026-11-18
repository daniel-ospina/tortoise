# Archived sdk-method `create_edge` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: create_edge left the public SDK surface — privatised (body survives as `_create_edge`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
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
ARCHIVED_SYMBOL = 'create_edge'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def create_edge(self, relation: str, from_id: str, to_id: str) -> dict:
        """Create a typed structural edge (epic #888 W2, PR #912).

        Reification rule (ontology v3.5 §8): structural edges stay PLAIN — no
        operator is created (operator iff mitigation, or Point↔Point
        support/contradict). Lazy promotion: when mitigation becomes needed,
        create the operator via create_operator and mitigate it with
        mitigate_operator(id, reason, strength=...). (The SDK method
        ``operator_action(action='mitigate')`` that used to wrap that call was
        deleted in #4282; the MCP tool ``tortoise_operator_action`` survives —
        it is not an SDK method.)

        ``relation`` must be one of the typed structural relations:
        performs, produces, uses, authoredBy, ownedBy, managedBy, hasMember,
        holdsRole, memberOf, reportsTo, participatesIn, hasPart, related,
        dependsOn, references, wasDerivedFrom, aboutSubject, aboutObject,
        aboutEvent, aboutDocument, aboutSource, aboutAction
        (``from``/``to`` are Python keywords — mapped to from_id/to_id).

        Returns {edge: {relation, from, to}, created: bool, nudges: [...]}.

        ⚠️ **A derivation ``references`` edge minted HERE carries no
        ``sourceVersion`` anchor, and nothing will add one later.** ``references``
        is in the allowlist above, and this method writes the edge BARE: it takes
        no property parameter (neither do the MCP tool ``tortoise_create_edge``
        nor ``projection.edges._EdgeHandlers.create_edge``), and ``create_source``
        refuses a caller-supplied ``sourceVersion`` — so the anchor cannot be
        supplied from the outside. The provenance writers
        (``link_source_to_entity`` / ``link_source_to_event`` /
        ``link_source_to_legacy_event``) are the only writers that stamp it, and
        their stamp is ``ON CREATE``-only, so a bare edge created here also
        PRE-EMPTS them: the later ``MERGE`` matches the existing edge and the
        clause never fires. A currency read of such an edge is ``unknown``
        (honest — never ``current``), and no writer repairs it: the only repair
        the SDK offers is deleting the edge and re-linking through a provenance
        writer (raw Cypher could ``SET`` the property, but that is not a repair
        path this SDK exposes). This is the documented boundary, not an oversight:
        option 3 of `#5213`; `docs/architecture/STORAGE-ARCHITECTURE.md` §9.6
        ("a pipeline guarantee, NOT a graph invariant") carries the decision, and
        `tests/test_references_boundary_5213.py` pins this behaviour — read it
        before re-routing this method through the anchor.
        """
        proj = self._get_proj()

        # R5 class 4 (#9553): the RECORD carrier for an edge minted here is
        # emitted at the MINT — `projection.edges._EdgeHandlers.create_edge` /
        # `create_about_edge` → `_record_structural_edges` — not at this door.
        #
        # ⛔ ONE PRODUCER, ON PURPOSE, AND THIS DOOR IS NOT IT. `#9827` shipped
        # this door's OWN emission (resolve both endpoints, `_emit_event`); this
        # change puts the emission one level down, at the mint, because the door
        # cannot reach the writers that matter: `create_owned_by`/
        # `create_managed_by`/`create_authored_by` are `return self.create_edge(…)`
        # on the PROJECTION (so `sdk.create_object(…, ownedBy=…)` — the commonest
        # `ownedBy` producer — never runs a door), the ingest bundle-connection
        # writer and the `wasDerivedFrom` writer call `proj.create_edge`
        # directly, and `create_about_edge` is a different door entirely. With
        # BOTH emissions in place one edge journals TWO `DirectEdgeCreated`
        # records, so the door-level one was removed when the two changes met.
        #
        # What the mint-level producer inherits from this door's review rounds and
        # keeps: the descriptor names the endpoints `_resolve_entity` returned
        # (never the caller's spelling), it carries NO `direction`/attrs (§9.6 —
        # the fold would SET what live never had), and it is emitted on every
        # successful write rather than created-only (`_EdgeHandlers.create_edge`
        # returns `MERGE … RETURN count(*) > 0`, truthy on a dedup hit, which is
        # what closes the crash window between the write and its record).
        created = proj.create_edge(from_id, to_id, relation)  # validates relation
        return {
            "edge": {"relation": relation, "from": from_id, "to": to_id},
            "created": created,
            "nudges": self._nudge_candidates(
                relation, exclude_ids=[from_id, to_id]),
        }
