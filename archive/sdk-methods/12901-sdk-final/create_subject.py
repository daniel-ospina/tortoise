# Archived sdk-method `create_subject` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_create_subject`; recorded destination `create_entity` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'create_subject'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def create_subject(self, name: str, subjectKind: str | None = None,
                       *, intent: str | None = None, **props) -> dict:
        """Thin alias for create_entity(type='subject') — epic #888 W2.

        #9964: the KIND decides the layer. An ``objectKind``-declared kind
        (``core:product``, or ``core:organization``'s mirror) routes the write
        to a ``:Object`` and the result carries a ``kind_layer_routed``
        advisory; a kind declared on another axis is reported as
        ``kind_other_layer`` and lands as asked (warn-not-block, #1934).

        ``subjectKind`` defaults to ``None`` (**absent**, not ``"other"**) since
        #9572: the alias used to fabricate ``"other"``, so a bare re-create
        shipped ``subject_kind="other"`` on the record and the fold's ON MATCH
        ``coalesce($sk, s.subjectKind)`` clobbered the stored, user-authored
        kind. The ``'other'`` default now comes from the fold's ON CREATE arm,
        which is the only place it belongs — see ``sdk._entity_door_props``.
        A BLANK ``subjectKind`` (``""`` / ``"   "``) is "not supplied" too
        (#9572 review P2): ``kind_fallbacks.named_kind`` normalizes it, so it
        can neither reset a stored kind nor store ``""``.
        """
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        _drop_reserved_emit_props(props)  # #2296: strip BEFORE the relay splat
        if "is_episodic" in props:  # #1486: server-managed (quota discriminator)
            raise ValueError(
                "'is_episodic' is a server-managed field and cannot be set via props.")
        subjectKind = named_kind(subjectKind)
        if subjectKind is not None:
            props["subjectKind"] = subjectKind
        # ⛔ BUILD THE BAG, never `intent=intent, **props`: `props` is the caller's
        # bag after `_coerce_props`, which flattens the documented
        # `props={"k": v}` convention — so a caller whose DATA carries an
        # `intent` key would hit "got multiple values for keyword argument
        # 'intent'" AT THE RELAY, one frame before `create_entity` can put the
        # value back (the #2296 relay-collision class, reopened by adding a
        # parameter and not a strip). An explicit `intent` argument wins; a
        # non-token one is restored as a prop by `create_entity` itself.
        _kw = dict(props)
        if intent is not None:
            _kw["intent"] = intent
        return self._flat_entity_result(
            self.create_entity("subject", name, **_kw))
