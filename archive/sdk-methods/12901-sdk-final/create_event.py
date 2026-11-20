# Archived sdk-method `create_event` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_create_event`; recorded destination `create_entity` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'create_event'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def create_event(self, name: str, eventKind: str, *, is_episodic: bool | None = None,
                      _server_id: str | None = None, **props) -> dict:
        """Create an Event node (alias for create_entity(type='event')).

        If aboutSubject, aboutObject, aboutPoint, or aboutDocument are provided
        in **props, they are extracted and wired as graph edges:
          (Event)-[:aboutSubject]->(Subject)
          (Event)-[:aboutObject]->(Object)
          (Event)-[:aboutPoint]->(Point)
          (Event)-[:aboutDocument]->(Source)   # D10: a document IS a :Source
        rather than stored as string properties.
        """
        _coerce_props(props)  # accept MCP-style nested props= dict (#218)
        _drop_reserved_emit_props(props)  # #2296: strip BEFORE the relay splat
        # Second-model gate P2: a single-nested props={"is_episodic": true}
        # flattens here and would splat-bind create_entity's explicit param —
        # reject it; the explicit `is_episodic` param (internal callers) is the
        # only channel.
        if "is_episodic" in props:
            raise ValueError(
                "'is_episodic' is a server-managed field and cannot be set via props.")
        # W5 Phase F (#2104, review r1): the explicit ``_server_id`` param must
        # never be reachable through the props passthrough — the MCP boundary
        # denylist catches top-level keys, this catches the nested-props
        # flatten (a props={"props": {...}} dict would otherwise splat-bind the
        # keyword-only param after _coerce_props).
        if "_server_id" in props:
            raise ValueError(
                "'_server_id' is a server-managed field and cannot be set "
                "via props.")
        if is_episodic is not None:
            props["is_episodic"] = is_episodic  # server-managed (explicit param only)
        return self._flat_entity_result(
            self.create_entity("event", name,
                               eventKind=eventKind, _server_id=_server_id,
                               **props))
