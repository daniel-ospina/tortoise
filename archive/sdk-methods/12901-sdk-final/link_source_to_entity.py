# Archived sdk-method `link_source_to_entity` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_link_source_to_entity`; recorded destination `link_entities` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'link_source_to_entity'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def link_source_to_entity(self, source_url: str, entity_id: str, entity_label: str, source_kind: str = "document") -> None:
        """Create Source → Entity references edge (Ontology v3.1 §3.4).

        Auto-creates the Source node if it doesn't exist (MERGE + ON CREATE SET)
        so the edge works even when no Point extracted the source yet (#205).

        Args:
            source_url: the Source node's url (auto-created if missing)
            entity_id: the Source/Event/Object node id the source references
            entity_label: the entity label (Source|Event|Object) for the MATCH.
                The retired ``"Document"`` is accepted as a DEPRECATED ALIAS and
                resolved to ``Source`` (D10, ONTOLOGY v3.15 §4.4).
                ⚠️ The alias is NOT equivalent to ``"Source"`` for the caller:
                it also selects the DERIVATION relation, so the link takes the
                optional ``sourceVersion`` anchor described below, while
                ``"Source"`` means referential containment and carries no
                ``sourceVersion``. Pass ``"Document"`` when the link's meaning is
                "this target was read from that source".
            source_kind: sourceKind to set on auto-created Source (default: "document")

        Anchor (#5199): a DERIVATION link (``Event`` | ``Document``) records the
        source version its target was read from as an optional ``sourceVersion``,
        read HERE from the source's own ``contentHash`` so no caller passes it and
        this signature is unchanged. Written ``ON CREATE`` only. ``Object``
        (identity/mention) and ``Source`` (containment) links carry no
        ``sourceVersion`` — the ruling scopes the claim to the ANCHOR, not to a
        general emptiness claim on those edges, so do not restate it as "these
        links are property-free". See ``docs/architecture/STORAGE-ARCHITECTURE.md``
        §9.6.

        Raises:
            ValueError: if entity_label is not one of Source, Event, Object
                (Action was dissolved in Ontology v3.0).

        A ``Source`` target that resolves to the SAME node as ``source_url``
        writes NO ``references`` self-loop (#5151): since D10 a document is a
        ``:Source``, so equal keys collapse source and entity onto one node, and
        the guard lives in the projection writer where the edge is written — the
        ``:Source`` itself is still merged, unchanged.
        """
        proj = self._get_proj()
        # ── #5475 (residual): the SECOND writer of ``sourceKind`` ──
        # ``create_source`` is gated at the entity funnel, but this public method
        # auto-creates the ``:Source`` itself (``MERGE … ON CREATE SET
        # s.sourceKind=$sk``, ``projection/edges.py``), so it was a second, ungated
        # writer of one of the properties ``graph_kind_namespaces`` mines — a
        # caller-chosen namespace, reachable from a public SDK method. Measured on
        # a ``dev``-only graph: ``link_source_to_entity(url, id, 'Object',
        # source_kind='marketing:campaign')`` left ``['dev','marketing']``.
        # Refused BEFORE the call, not conditionally on the node's existence: a
        # read-then-write decision here would be a race, and the value is only
        # honoured ON CREATE, so existence is not ours to predict.
        self._refuse_unapproved_kind("sourceKind", source_kind)
        proj.link_source_to_entity(source_url, entity_id, entity_label, source_kind)
