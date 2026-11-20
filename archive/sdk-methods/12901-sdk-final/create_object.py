# Archived sdk-method `create_object` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_create_object`; recorded destination `create_entity` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'create_object'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def create_object(self, name: str, objectKind: str | None = None,
                      *, intent: str | None = None, **props) -> dict:
        """Thin alias for create_entity(type='object') — epic #888 W2.

        #9964: the KIND decides the layer. ``create_object(name,
        objectKind='core:organization')`` writes a ``:Subject`` (a §5 Subject
        kind cannot be stored as an ``:Object`` — the Subject read would never
        see it) and returns a ``kind_layer_routed`` advisory; a kind declared
        on another axis is reported as ``kind_other_layer`` and lands as asked
        (warn-not-block, #1934).

        ``objectKind`` defaults to ``None`` (**absent**, not ``"other"**) since
        #9572 — the Object twin of ``create_subject``'s fabricated default.
        Blank is absent here too (#9572 review P2) — ``named_kind``.

        #12060: the same kind guard as ``create_subject`` — an undeclared-kind
        write whose name matches a declared common confusion is filed at its
        right kind/layer with ``kind_redirect`` on the node; ``intent=
        "as_declared"`` keeps it as asked and records the override.
        """
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        _drop_reserved_emit_props(props)  # #2296: strip BEFORE the relay splat
        if "is_episodic" in props:  # #1486: server-managed (quota discriminator)
            raise ValueError(
                "'is_episodic' is a server-managed field and cannot be set via props.")
        objectKind = named_kind(objectKind)
        if objectKind is not None:
            props["objectKind"] = objectKind
        _kw = dict(props)  # the same relay-collision guard as create_subject
        if intent is not None:
            _kw["intent"] = intent
        return self._flat_entity_result(
            self.create_entity("object", name, **_kw))
